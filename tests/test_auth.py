import pytest

from atlas.config import Settings
from atlas.services.auth import (
    AuthError,
    authenticate_user,
    issue_access_token,
    parse_demo_users,
    verify_access_token,
)


def test_parse_demo_users_splits_pipe_entries() -> None:
    users = parse_demo_users(" alice:one |bob:two ")
    assert users == {"alice": "one", "bob": "two"}


def test_parse_demo_users_rejects_missing_colon() -> None:
    with pytest.raises(AuthError, match="username:password"):
        parse_demo_users("alice")


def test_parse_demo_users_rejects_empty_password() -> None:
    with pytest.raises(AuthError, match="cannot be empty"):
        parse_demo_users("alice:")


def test_authenticate_and_token_round_trip() -> None:
    settings = Settings(
        atlas_auth_secret="unit-test-secret",
        atlas_demo_users="alice:correct-horse|bob:other",
    )
    assert authenticate_user(settings, "alice", "wrong") is None
    assert authenticate_user(settings, "alice", "correct-horse") == "alice"
    token = issue_access_token(settings, "alice")
    assert verify_access_token(settings, token) == "alice"
    assert verify_access_token(settings, "not-a-token") is None
    assert verify_access_token(settings, "") is None


def test_malformed_demo_users_fail_closed() -> None:
    settings = Settings(
        atlas_auth_secret="unit-test-secret",
        atlas_demo_users="not-a-user",
    )
    assert authenticate_user(settings, "alice", "x") is None
    token = issue_access_token(settings, "alice")
    assert verify_access_token(settings, token) is None


def test_verify_rejects_empty_secret_and_unknown_subject() -> None:
    settings = Settings(
        atlas_auth_secret="unit-test-secret",
        atlas_demo_users="alice:correct-horse",
    )
    token = issue_access_token(settings, "alice")
    blank = Settings(atlas_auth_secret="", atlas_demo_users="alice:correct-horse")
    assert verify_access_token(blank, token) is None
    other_users = Settings(
        atlas_auth_secret="unit-test-secret",
        atlas_demo_users="bob:other",
    )
    assert verify_access_token(other_users, token) is None


def test_token_from_other_secret_is_rejected() -> None:
    settings = Settings(
        atlas_auth_secret="unit-test-secret",
        atlas_demo_users="alice:correct-horse",
    )
    other = Settings(
        atlas_auth_secret="different-secret",
        atlas_demo_users="alice:correct-horse",
    )
    token = issue_access_token(settings, "alice")
    assert verify_access_token(other, token) is None
