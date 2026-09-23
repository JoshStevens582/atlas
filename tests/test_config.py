import pytest

from atlas.config import (
    INSECURE_DEFAULT_AUTH_SECRET,
    InsecureAuthSecretError,
    Settings,
    require_secure_auth_secret,
)


def test_rejects_shipped_default_secret() -> None:
    settings = Settings(atlas_auth_secret=INSECURE_DEFAULT_AUTH_SECRET)
    with pytest.raises(InsecureAuthSecretError, match="shipped default"):
        require_secure_auth_secret(settings)


def test_rejects_secret_under_min_bytes() -> None:
    settings = Settings(atlas_auth_secret="short-secret")
    with pytest.raises(InsecureAuthSecretError, match="under 32 bytes"):
        require_secure_auth_secret(settings)


def test_accepts_long_random_secret() -> None:
    settings = Settings(atlas_auth_secret="a" * 32)
    require_secure_auth_secret(settings)  # must not raise


def test_empty_secret_is_the_deliberate_disabled_state_not_an_error() -> None:
    """Empty ATLAS_AUTH_SECRET means "auth intentionally disabled" — every
    auth route already returns 503 for that case (see
    api/routers/auth.py::_require_auth_secret). Startup must not crash here,
    only on an insecure-but-non-empty secret."""
    settings = Settings(atlas_auth_secret="")
    require_secure_auth_secret(settings)  # must not raise
