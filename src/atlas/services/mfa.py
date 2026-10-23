"""Authenticator-app MFA (production step 16).

Off until the signed-in person turns it on. Setup asks for the password
again so a stolen session token cannot enroll a new phone. The secret is
encrypted with the Atlas API key before it is stored. Recovery codes are
hashed, shown once, and each works a single time.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import segno
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from atlas.config import Settings
from atlas.repositories.mfa_repo import MfaRepository
from atlas.repositories.user_repo import UserRepository
from atlas.services.auth import AuthError, verify_password
from atlas.services.totp import (
    generate_base32_secret,
    matching_totp_step,
    provisioning_uri,
)

logger = logging.getLogger("atlas.mfa")

MFA_ISSUER = "Atlas"
RECOVERY_CODE_COUNT = 8
_RECOVERY_ALPHABET = "abcdefghijkmnpqrstuvwxyz23456789"


class MfaRejected(AuthError):
    """Password or authenticator code did not match."""


@dataclass(frozen=True)
class MfaSetup:
    secret: str
    otpauth_uri: str
    qr_data_uri: str


def encrypt_totp_secret(settings: Settings, secret: str) -> str:
    token = _fernet(settings).encrypt(secret.encode("utf-8"))
    return token.decode("ascii")


def decrypt_totp_secret(settings: Settings, stored: str) -> str:
    try:
        raw = _fernet(settings).decrypt(stored.encode("ascii"))
    except (InvalidToken, ValueError, UnicodeError) as exc:
        raise AuthError("Authenticator data could not be read.") from exc
    return raw.decode("utf-8")


def generate_recovery_code() -> str:
    raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(8))
    return f"{raw[:4]}-{raw[4:]}"


def normalize_recovery_code(code: str) -> str:
    return "".join(character for character in code.strip().lower() if character.isalnum())


def hash_recovery_code(code: str) -> str:
    normalized = normalize_recovery_code(code)
    if len(normalized) != 8:
        raise ValueError("Recovery code must be 8 characters.")
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _fernet(settings: Settings) -> Fernet:
    digest = hashlib.sha256(settings.atlas_auth_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _qr_data_uri(payload: str) -> str:
    return segno.make(payload, error="m").svg_data_uri(scale=4)


def _second_factor(code: str) -> tuple[str, str] | None:
    compact = "".join(code.split())
    if compact.isdigit() and len(compact) == 6:
        return ("totp", compact)
    normalized = normalize_recovery_code(code)
    if len(normalized) == 8:
        return ("recovery", normalized)
    return None


async def begin_mfa_setup(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    username: str,
    password: str,
) -> MfaSetup:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
        if user is None:
            raise AuthError("Account not found.")
        if not verify_password(password, user.password_hash):
            raise MfaRejected("Password did not match.")
        if user.mfa_enabled_at is not None:
            raise AuthError(
                "Authenticator is already on. Turn it off before setting it up again."
            )
        secret = generate_base32_secret()
        encrypted = encrypt_totp_secret(settings, secret)
        await MfaRepository(session).save_pending_secret(user, encrypted)

    uri = provisioning_uri(secret=secret, username=username, issuer=MFA_ISSUER)
    return MfaSetup(secret=secret, otpauth_uri=uri, qr_data_uri=_qr_data_uri(uri))


async def confirm_mfa_setup(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    username: str,
    code: str,
    now: datetime | None = None,
) -> list[str]:
    moment = now or datetime.now(UTC)
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
        if user is None:
            raise AuthError("Account not found.")
        if user.mfa_enabled_at is not None:
            raise AuthError("Authenticator is already on.")
        if not user.mfa_secret:
            raise AuthError("Start authenticator setup first.")
        secret = decrypt_totp_secret(settings, user.mfa_secret)
        step = matching_totp_step(secret, code, now=int(moment.timestamp()))
        if step is None:
            raise MfaRejected(
                "That code did not match. Check the time on your phone and try again."
            )
        codes = [generate_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
        confirmed = await MfaRepository(session).confirm(
            user,
            enabled_at=moment,
            last_step=step,
            code_hashes=[hash_recovery_code(item) for item in codes],
        )
    if not confirmed:
        raise AuthError("Authenticator is already on.")
    logger.info("mfa enabled username=%s", username)
    return codes


async def verify_totp_code(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    username: str,
    code: str,
    now: datetime | None = None,
) -> None:
    """Check a 6-digit authenticator code only (recovery codes do not count)."""
    moment = now or datetime.now(UTC)
    compact = "".join(code.split())
    if not (compact.isdigit() and len(compact) == 6):
        raise MfaRejected("Enter the 6-digit code from your authenticator app.")
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
        if user is None or user.mfa_enabled_at is None or not user.mfa_secret:
            raise MfaRejected("That code did not match.")
        secret = decrypt_totp_secret(settings, user.mfa_secret)
        step = matching_totp_step(secret, compact, now=int(moment.timestamp()))
        if step is None:
            raise MfaRejected("That code did not match.")
        repo = MfaRepository(session)
        claimed = await repo.claim_totp_step(user.id, step)
        if not claimed:
            raise MfaRejected("That code was already used. Wait for the next code.")


async def regenerate_recovery_codes(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    username: str,
    password: str,
    code: str,
    now: datetime | None = None,
) -> list[str]:
    """Issue a new set of recovery codes. Old unused codes stop working."""
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
        if user is None:
            raise AuthError("Account not found.")
        if user.mfa_enabled_at is None:
            raise AuthError("Turn authenticator on before generating recovery codes.")
        if not verify_password(password, user.password_hash):
            raise MfaRejected("Password did not match.")
    await verify_totp_code(
        session_factory,
        settings,
        username=username,
        code=code,
        now=now,
    )
    codes = [generate_recovery_code() for _ in range(RECOVERY_CODE_COUNT)]
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
        if user is None or user.mfa_enabled_at is None:
            raise AuthError("Account not found.")
        await MfaRepository(session).replace_recovery_codes(
            user.id,
            [hash_recovery_code(item) for item in codes],
        )
    logger.info("mfa recovery codes regenerated username=%s", username)
    return codes


async def accept_second_factor(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    username: str,
    code: str,
    now: datetime | None = None,
) -> None:
    moment = now or datetime.now(UTC)
    factor = _second_factor(code)
    if factor is None:
        raise MfaRejected("That code did not match.")
    kind, value = factor
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
        if user is None or user.mfa_enabled_at is None or not user.mfa_secret:
            raise MfaRejected("That code did not match.")
        repo = MfaRepository(session)
        if kind == "totp":
            secret = decrypt_totp_secret(settings, user.mfa_secret)
            step = matching_totp_step(secret, value, now=int(moment.timestamp()))
            if step is None:
                raise MfaRejected("That code did not match.")
            claimed = await repo.claim_totp_step(user.id, step)
            if not claimed:
                raise MfaRejected("That code was already used. Wait for the next code.")
            return
        used = await repo.consume_recovery_code(
            user.id,
            hash_recovery_code(value),
            used_at=moment,
        )
    if not used:
        raise MfaRejected("That code did not match.")


async def disable_mfa(
    session_factory: async_sessionmaker[AsyncSession],
    settings: Settings,
    *,
    username: str,
    password: str,
    code: str,
    now: datetime | None = None,
) -> None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
        if user is None:
            raise AuthError("Account not found.")
        if user.mfa_enabled_at is None:
            raise AuthError("Authenticator is already off.")
        if not verify_password(password, user.password_hash):
            raise MfaRejected("Password did not match.")
    await accept_second_factor(
        session_factory,
        settings,
        username=username,
        code=code,
        now=now,
    )
    async with session_factory() as session:
        user = await UserRepository(session).get_by_username(username)
        if user is None:
            raise AuthError("Account not found.")
        await MfaRepository(session).clear(user)
    logger.info("mfa disabled username=%s", username)
