"""Shared pytest hooks for the Atlas test suite."""

from __future__ import annotations

from collections.abc import Iterator

import bcrypt
import pytest

_ORIGINAL_GENSALT = bcrypt.gensalt


def _fast_gensalt(*, rounds: int = 12, prefix: bytes = b"2b") -> bytes:
    # Default bcrypt rounds are fine on the server; tests only need correctness.
    return _ORIGINAL_GENSALT(rounds=4, prefix=prefix)


@pytest.fixture(autouse=True)
def _lower_bcrypt_cost_for_tests(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(bcrypt, "gensalt", _fast_gensalt)
    yield
