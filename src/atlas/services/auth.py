"""Real accounts: bcrypt-hashed passwords in the ``users`` table.

Signup and login both go through here. Demo accounts (alice/bob) are just
ordinary rows in the same table, seeded once on startup (see
``seed_demo_users``) — the login screen shows their password so recruiters
can click straight in, but nothing about how they're stored is special-cased.

Session tokens (``issue_access_token`` / ``verify_access_token``) are
unchanged from before: a signed, time-limited token. We deliberately do not
hit the database on every authenticated request to re-check the user still
exists — the signature + 7-day expiry *is* the session. That trades instant
revocation (e.g. an admin deleting a user mid-session) for not adding a DB
round trip to every single API call. Worth calling out as a real design
choice, not an oversight, if it comes up.
"""

from __future__ import annotations

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings
from atlas.repositories.user_repo import UsernameTakenError, UserRepository

TOKEN_SALT = "atlas-access-v1"
TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 7

MIN_USERNAME_LENGTH = 3
MIN_PASSWORD_LENGTH = 8

# Used to check a password against when the username doesn't exist, so a
# login attempt for a real user and a made-up user take the same amount of
# time — otherwise an attacker can tell which usernames exist by timing.
_DUMMY_HASH = bcrypt.hashpw(b"not-a-real-password", bcrypt.gensalt()).decode("ascii")


class AuthError(Exception):
    pass


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
) -> str:
    username = username.strip()
    if len(username) < MIN_USERNAME_LENGTH:
        raise AuthError(f"Username must be at least {MIN_USERNAME_LENGTH} characters.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters.")

    password_hash = hash_password(password)
    async with session_factory() as session:
        repo = UserRepository(session)
        if await repo.get_by_username(username) is not None:
            raise AuthError(f"Username '{username}' is already taken.")
        try:
            user = await repo.create(username, password_hash)
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


def issue_access_token(settings: Settings, username: str) -> str:
    serializer = URLSafeTimedSerializer(settings.atlas_auth_secret, salt=TOKEN_SALT)
    return str(serializer.dumps({"sub": username}))


def verify_access_token(settings: Settings, token: str) -> str | None:
    if not token.strip() or not settings.atlas_auth_secret:
        return None
    serializer = URLSafeTimedSerializer(settings.atlas_auth_secret, salt=TOKEN_SALT)
    try:
        payload = serializer.loads(token, max_age=TOKEN_MAX_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    if not isinstance(payload, dict):
        return None
    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject:
        return None
    return subject
