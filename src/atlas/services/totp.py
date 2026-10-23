"""Time-based one-time passwords (RFC 6238, HMAC-SHA1, 6 digits, 30 seconds).

The authenticator app and Atlas share a secret. Each 30-second step both
sides compute the same 6-digit code. Atlas accepts the current step and one
step either side so a phone clock that is slightly off still works.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

TOTP_DIGITS = 6
TOTP_STEP_SECONDS = 30
TOTP_WINDOW_STEPS = 1
_SHA1_DIGEST = hashlib.sha1


def hotp(key: bytes, counter: int, *, digits: int = TOTP_DIGITS) -> str:
    if counter < 0:
        raise ValueError("counter must be zero or greater.")
    if digits < 6 or digits > 8:
        raise ValueError("digits must be 6, 7, or 8.")
    message = struct.pack(">Q", counter)
    digest = hmac.new(key, message, _SHA1_DIGEST).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    code = binary % (10**digits)
    return f"{code:0{digits}d}"


def totp_code(
    key: bytes,
    for_time: int,
    *,
    step_seconds: int = TOTP_STEP_SECONDS,
    digits: int = TOTP_DIGITS,
) -> str:
    if for_time < 0:
        raise ValueError("for_time must be zero or greater.")
    if step_seconds <= 0:
        raise ValueError("step_seconds must be positive.")
    return hotp(key, for_time // step_seconds, digits=digits)


def generate_base32_secret() -> str:
    """160-bit secret, base32, no padding — what authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def decode_base32_secret(secret: str) -> bytes:
    compact = "".join(secret.split()).upper()
    if not compact:
        raise ValueError("Authenticator secret is empty.")
    pad = "=" * ((8 - len(compact) % 8) % 8)
    try:
        return base64.b32decode(compact + pad, casefold=True)
    except ValueError as exc:
        raise ValueError("Authenticator secret is not valid base32.") from exc


def current_totp_code(secret: str, *, now: int) -> str:
    return totp_code(decode_base32_secret(secret), now)


def matching_totp_step(
    secret: str,
    code: str,
    *,
    now: int,
    step_seconds: int = TOTP_STEP_SECONDS,
    window: int = TOTP_WINDOW_STEPS,
    digits: int = TOTP_DIGITS,
) -> int | None:
    """Return the accepted time-step, or None when the code does not match."""
    cleaned = "".join(code.split())
    if len(cleaned) != digits or not cleaned.isdigit():
        return None
    if now < 0 or step_seconds <= 0 or window < 0:
        return None
    try:
        key = decode_base32_secret(secret)
    except ValueError:
        return None
    current = now // step_seconds
    for delta in range(-window, window + 1):
        candidate = current + delta
        if candidate < 0:
            continue
        expected = hotp(key, candidate, digits=digits)
        if hmac.compare_digest(expected, cleaned):
            return candidate
    return None


def provisioning_uri(*, secret: str, username: str, issuer: str) -> str:
    label = quote(f"{issuer}:{username}")
    return (
        f"otpauth://totp/{label}?secret={quote(secret)}&issuer={quote(issuer)}"
        "&algorithm=SHA1&digits=6&period=30"
    )
