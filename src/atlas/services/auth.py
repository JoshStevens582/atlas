"""Real accounts: bcrypt-hashed passwords in the ``users`` table.

Signup and login both go through here. Demo accounts (alice/bob) are just
ordinary rows in the same table, seeded once on startup (see
``seed_demo_users``) — the login screen shows their password so recruiters
can click straight in, but nothing about how they're stored is special-cased.

Session tokens (``issue_access_token`` / ``verify_access_token``) are real
JWTs (HS256, signed with ``ATLAS_AUTH_SECRET``): standard header.payload.signature
format, `sub` + `iat` + `exp` claims, verified with PyJWT. New tokens also
carry ``typ=access``. A short-lived ``typ=mfa`` token only proves the
password was accepted and must not open a session. Tokens issued before
that claim existed have no ``typ`` and still verify until they expire.
Authenticated routes then load the user row so a disable or role change
applies without waiting for the 7-day token to expire. The JWT is still
who you are; the row is whether that account may still act.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import bcrypt
import jwt
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings
from atlas.db.models import User
from atlas.repositories.user_repo import ROLE_ADMIN, UsernameTakenError, UserRepository
from atlas.schemas.auth import UserRole

JWT_ALGORITHM = "HS256"
# Default access lifetime when settings do not override (step 17).
DEFAULT_ACCESS_TOKEN_TTL_SECONDS = 15 * 60
TOKEN_TYPE_ACCESS = "access"
TOKEN_TYPE_MFA = "mfa"

MIN_USERNAME_LENGTH = 3
MIN_PASSWORD_LENGTH = 8

# Used to check a password against when the username doesn't exist, so a
# login attempt for a real user and a made-up user take the same amount of
# time — otherwise an attacker can tell which usernames exist by timing.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode("ascii")


class AuthError(Exception):
    pass


def user_needs_email_verification(user: User) -> bool:
    """Login waits until signup email is verified (demo users have no email)."""
    return bool(user.email) and user.email_verified_at is None


def user_role(user: User) -> UserRole:
    return "admin" if user.role == ROLE_ADMIN else "user"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        # Malformed/legacy hash in the row — never let that crash a login.
        return False


async def signup_user(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    password: str,
    *,
    email: str,
) -> str:
    username = username.strip()
    if len(username) < MIN_USERNAME_LENGTH:
        raise AuthError(f"Username must be at least {MIN_USERNAME_LENGTH} characters.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    normalized_email = email.strip().lower()
    if not normalized_email or "@" not in normalized_email or normalized_email.startswith("@"):
        raise AuthError("A valid email address is required.")

    password_hash = hash_password(password)
    async with session_factory() as session:
        repo = UserRepository(session)
        if await repo.get_by_username(username) is not None:
            raise AuthError(f"Username '{username}' is already taken.")
        if await repo.get_by_email(normalized_email) is not None:
            raise AuthError("That email is already in use.")
        try:
            user = await repo.create(username, password_hash, email=normalized_email)
        except UsernameTakenError as exc:
            raise AuthError(str(exc)) from exc
    return user.username


async def authenticate_user(
    session_factory: async_sessionmaker[AsyncSession],
    username: str,
    password: str,
) -> str | None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username.strip())

    if user is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, user.password_hash):
        return None
    return user.username


async def seed_demo_users(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
) -> None:
    """Insert the demo accounts advertised on the login screen, once, as
    ordinary real rows — same table, same hashing, nothing special-cased."""
    for entry in settings.atlas_demo_users.split("|"):
        piece = entry.strip()
        if not piece or ":" not in piece:
            continue
        username, password = (part.strip() for part in piece.split(":", 1))
        if not username or not password:
            continue
        async with session_factory() as session:
            repo = UserRepository(session)
            if await repo.get_by_username(username) is not None:
                continue
            try:
                await repo.create(username, hash_password(password))
            except UsernameTakenError:
                pass  # another worker/process seeded it first; fine


def issue_access_token(
    settings: Settings,
    username: str,
    *,
    now: datetime | None = None,
) -> str:
    moment = now or datetime.now(UTC)
    ttl = settings.access_token_ttl_seconds
    payload = {
        "sub": username,
        "iat": moment,
        "exp": moment + timedelta(seconds=ttl),
        "typ": TOKEN_TYPE_ACCESS,
    }
    return jwt.encode(payload, settings.atlas_auth_secret, algorithm=JWT_ALGORITHM)


def issue_mfa_challenge_token(settings: Settings, username: str) -> str:
    """Short-lived proof that the password was correct. Not a session."""
    now = datetime.now(UTC)
    payload = {
        "sub": username,
        "iat": now,
        "exp": now + timedelta(seconds=settings.mfa_challenge_ttl_seconds),
        "typ": TOKEN_TYPE_MFA,
    }
    return jwt.encode(payload, settings.atlas_auth_secret, algorithm=JWT_ALGORITHM)


def _decode_token(settings: Settings, token: str) -> dict[str, object] | None:
    if not token.strip() or not settings.atlas_auth_secret:
        return None
    try:
        # algorithms= is required, not optional: without pinning it, a
        # forged token could switch algorithms (e.g. to "none") and skip
        # signature verification entirely — a real, historical JWT bug class.
        payload = jwt.decode(
            token, settings.atlas_auth_secret, algorithms=[JWT_ALGORITHM]
        )
    except jwt.InvalidTokenError:
        return None
    if not isinstance(payload, dict):
        return None
    decoded: dict[str, object] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            return None
        decoded[key] = value
    return decoded


def _subject(payload: dict[str, object]) -> str | None:
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        return None
    return subject


def verify_access_token(settings: Settings, token: str) -> str | None:
    payload = _decode_token(settings, token)
    if payload is None:
        return None
    # Missing typ: tokens issued before MFA. typ=mfa must not open a session.
    token_type = payload.get("typ")
    if token_type not in (None, TOKEN_TYPE_ACCESS):
        return None
    return _subject(payload)


def verify_mfa_challenge_token(settings: Settings, token: str) -> str | None:
    payload = _decode_token(settings, token)
    if payload is None:
        return None
    if payload.get("typ") != TOKEN_TYPE_MFA:
        return None
    return _subject(payload)
