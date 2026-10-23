"""RFC 6238 vectors and the window Atlas accepts."""

from datetime import UTC, datetime

import pytest

from atlas.services.totp import (
    current_totp_code,
    decode_base32_secret,
    generate_base32_secret,
    hotp,
    matching_totp_step,
    provisioning_uri,
    totp_code,
)

# Appendix B, HMAC-SHA1. The 8-digit codes are the published vectors.
_RFC_KEY = b"12345678901234567890"
_RFC_VECTORS = (
    (59, "94287082"),
    (1111111109, "07081804"),
    (1111111111, "14050471"),
    (1234567890, "89005924"),
    (2000000000, "69279037"),
    (20000000000, "65353130"),
)


@pytest.mark.parametrize(("for_time", "expected"), _RFC_VECTORS)
def test_rfc_6238_sha1_vectors(for_time: int, expected: str) -> None:
    assert totp_code(_RFC_KEY, for_time, digits=8) == expected
    assert totp_code(_RFC_KEY, for_time) == expected[-6:]


def test_generated_secret_round_trips_and_matches_its_own_code() -> None:
    secret = generate_base32_secret()
    assert decode_base32_secret(secret)
    moment = int(datetime(2026, 10, 23, tzinfo=UTC).timestamp())
    code = current_totp_code(secret, now=moment)
    assert matching_totp_step(secret, code, now=moment) == moment // 30


def test_window_accepts_one_step_and_rejects_two() -> None:
    secret = generate_base32_secret()
    moment = int(datetime(2026, 10, 23, 12, 0, tzinfo=UTC).timestamp())
    previous = moment - 30
    too_old = moment - 60
    previous_code = current_totp_code(secret, now=previous)
    stale_code = current_totp_code(secret, now=too_old)
    assert matching_totp_step(secret, f"{previous_code[:3]} {previous_code[3:]}", now=moment) == (
        previous // 30
    )
    assert matching_totp_step(secret, stale_code, now=moment) is None


def test_matching_rejects_bad_inputs() -> None:
    secret = generate_base32_secret()
    now = 1_700_000_000
    assert matching_totp_step(secret, "12345", now=now) is None
    assert matching_totp_step(secret, "abcdef", now=now) is None
    assert matching_totp_step("not-base32!!!", "123456", now=now) is None
    assert matching_totp_step(secret, "123456", now=-1) is None
    assert matching_totp_step("", "123456", now=now) is None


def test_provisioning_uri_names_atlas_and_the_account() -> None:
    uri = provisioning_uri(secret="SECRET", username="ada", issuer="Atlas")
    assert uri.startswith("otpauth://totp/Atlas%3Aada?")
    assert "secret=SECRET" in uri
    assert "issuer=Atlas" in uri
    assert "digits=6" in uri


def test_hotp_and_totp_reject_bad_arguments() -> None:
    with pytest.raises(ValueError, match="counter"):
        hotp(b"key", -1)
    with pytest.raises(ValueError, match="digits"):
        hotp(b"key", 0, digits=5)
    with pytest.raises(ValueError, match="for_time"):
        totp_code(b"key", -1)
    with pytest.raises(ValueError, match="step"):
        totp_code(b"key", 0, step_seconds=0)


def test_early_timestamp_does_not_look_at_a_negative_step() -> None:
    secret = generate_base32_secret()
    # now=10 is still step 0; the previous step would be negative and is skipped.
    code = current_totp_code(secret, now=0)
    assert matching_totp_step(secret, code, now=10) == 0
