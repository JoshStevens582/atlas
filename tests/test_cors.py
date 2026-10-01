from collections.abc import Mapping

from fastapi.testclient import TestClient

from atlas.config import Settings
from atlas.main import CORS_ALLOWED_HEADERS, CORS_ALLOWED_METHODS, app

# No `with`: the preflight is answered by the middleware, so startup is not needed.
client = TestClient(app)


def _preflight(
    origin: str, method: str, headers: str = "Authorization"
) -> tuple[int, Mapping[str, str]]:
    response = client.options(
        "/api/threads",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": method,
            "Access-Control-Request-Headers": headers,
        },
    )
    return response.status_code, response.headers


def test_the_dev_origin_is_allowed_with_the_headers_the_app_sends() -> None:
    status, headers = _preflight("http://localhost:5173", "GET", "Authorization, Content-Type")
    assert status == 200
    assert headers["access-control-allow-origin"] == "http://localhost:5173"


def test_another_website_is_not_allowed() -> None:
    status, headers = _preflight("https://evil.example", "GET")
    assert status == 400
    assert "access-control-allow-origin" not in headers


def test_a_method_the_app_never_uses_is_refused() -> None:
    assert _preflight("http://localhost:5173", "PUT")[0] == 400


def test_a_header_the_app_never_sends_is_refused() -> None:
    assert _preflight("http://localhost:5173", "GET", "X-Anything")[0] == 400


def test_the_allowed_lists_are_not_wildcards() -> None:
    assert "*" not in CORS_ALLOWED_METHODS
    assert "*" not in CORS_ALLOWED_HEADERS


def test_an_empty_origin_setting_allows_no_origin_at_all() -> None:
    assert Settings(cors_origins="").cors_origin_list == []
    assert Settings(cors_origins=" , ").cors_origin_list == []
