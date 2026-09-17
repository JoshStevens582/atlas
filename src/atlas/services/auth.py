from __future__ import annotations

import secrets

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from atlas.config import Settings

TOKEN_SALT = "atlas-access-v1"
TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 7


class AuthError(Exception):
    pass


def parse_demo_users(raw: str) -> dict[str, str]:
    users: dict[str, str] = {}
    for entry in raw.split("|"):
        piece = entry.strip()
        if not piece:
            continue
        if ":" not in piece:
            raise AuthError("Each demo user must be username:password.")
        username, password = piece.split(":", 1)
        username = username.strip()
        password = password.strip()
        if not username or not password:
            raise AuthError("Demo user username and password cannot be empty.")
        users[username] = password
    return users


def authenticate_user(settings: Settings, username: str, password: str) -> str | None:
    try:
        users = parse_demo_users(settings.atlas_demo_users)
    except AuthError:
        return None
    stored = users.get(username)
    if stored is None or not secrets.compare_digest(stored, password):
        return None
    return username


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
    try:
        users = parse_demo_users(settings.atlas_demo_users)
    except AuthError:
        return None
    if subject not in users:
        return None
    return subject
