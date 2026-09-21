from pydantic_settings import BaseSettings, SettingsConfigDict


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

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


def load_settings() -> Settings:
    return Settings()
