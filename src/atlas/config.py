from pydantic_settings import BaseSettings, SettingsConfigDict

# Shipped in this repo's source, so anyone who has read it knows this value —
# it must never be the secret an app actually runs with.
INSECURE_DEFAULT_AUTH_SECRET = "dev-only-change-me"
# RFC 7518 3.2: HS256 keys should be >= the hash output size (32 bytes).
MIN_AUTH_SECRET_BYTES = 32


class InsecureAuthSecretError(RuntimeError):
    """ATLAS_AUTH_SECRET is missing a real value at startup."""


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_chat_model: str = "gpt-4o-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    database_url: str = "sqlite+aiosqlite:///./data/atlas.db"
    chroma_path: str = "./data/chroma"
    sample_docs_dir: str = "./sample_docs"
    upload_dir: str = "./data/uploads"
    max_upload_bytes: int = 5 * 1024 * 1024
    chunk_size: int = 900
    chunk_overlap: int = 150
    retrieve_k: int = 5
    max_distance: float = 0.85
    history_window: int = 12
    max_tool_rounds: int = 3
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    atlas_auth_secret: str = "dev-only-change-me"
    atlas_demo_users: str = "alice:atlas-alice|bob:atlas-bob"
    redis_url: str = "redis://127.0.0.1:6379/0"
    ingest_queue_enabled: bool = True
    ingest_worker_embedded: bool = True
    ingest_job_ttl_seconds: int = 60 * 60 * 24
    ingest_queue_key: str = "atlas:ingest:queue"
    rate_limit_enabled: bool = True
    rate_limit_fail_closed: bool = True
    rate_limit_window_seconds: int = 60
    rate_limit_ask_per_minute: int = 10
    rate_limit_upload_per_minute: int = 5
    rate_limit_ask_per_day: int = 40
    rate_limit_upload_per_day: int = 15
    rate_limit_ask_global_per_day: int = 200
    answer_cache_enabled: bool = True
    answer_cache_ttl_seconds: int = 60 * 60

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


def load_settings() -> Settings:
    return Settings()


def require_secure_auth_secret(settings: Settings) -> None:
    """Refuse to start with the shipped default or a too-short JWT secret.

    An empty secret is a deliberate, already-handled "auth disabled" state
    (see api/routers/auth.py: ``_require_auth_secret`` returns 503 for every
    auth route in that case) — this only blocks the insecure-but-non-empty
    cases: the literal default from this file, or anything shorter than the
    HS256 output size.
    """
    secret = settings.atlas_auth_secret
    if not secret:
        return
    if secret == INSECURE_DEFAULT_AUTH_SECRET:
        raise InsecureAuthSecretError(
            "ATLAS_AUTH_SECRET is still the shipped default "
            f"({INSECURE_DEFAULT_AUTH_SECRET!r}). Anyone who has read this "
            "repo's source knows it, so it can be used to forge session "
            "JWTs. Set ATLAS_AUTH_SECRET to a random secret before starting "
            "the app."
        )
    if len(secret.encode("utf-8")) < MIN_AUTH_SECRET_BYTES:
        raise InsecureAuthSecretError(
            f"ATLAS_AUTH_SECRET is under {MIN_AUTH_SECRET_BYTES} bytes; "
            "JWTs signed with it can be brute-forced. Set a longer, random "
            "secret before starting the app."
        )
