from functools import lru_cache
import logging
import os
from pathlib import Path
from typing import Self

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator


BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")
logger = logging.getLogger(__name__)
DEFAULT_CHUNK_SIZE = 1600
DEFAULT_CHUNK_OVERLAP = 250


def validate_chunk_settings(chunk_size: int, chunk_overlap: int) -> None:
    if type(chunk_size) is not int or chunk_size <= 0:
        message = "chunk_size must be a positive integer."
    elif type(chunk_overlap) is not int or chunk_overlap < 0:
        message = "chunk_overlap must be a non-negative integer."
    elif chunk_overlap >= chunk_size:
        message = "chunk_overlap must be smaller than chunk_size."
    else:
        return
    logger.error(message)
    raise ValueError(message)


class Settings(BaseModel):
    app_name: str = "KQ Customer Support Chatbot API"
    app_version: str = "1.0.0"
    environment: str = Field(default="development")
    debug: bool = Field(default=True)
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"])

    firestore_project_id: str | None = None
    firebase_credentials_path: Path | None = None

    raw_data_dir: Path = BASE_DIR / "data" / "raw"
    processed_data_dir: Path = BASE_DIR / "data" / "processed"
    chroma_persist_dir: Path = BASE_DIR.parent / "chroma_db"
    rag_chunk_size: int = Field(default=DEFAULT_CHUNK_SIZE, strict=True)
    rag_chunk_overlap: int = Field(default=DEFAULT_CHUNK_OVERLAP, strict=True)

    embedding_model_name: str | None = None
    embedding_model_revision: str | None = None
    embedding_batch_size: int = Field(default=4, gt=0, le=64, strict=True)
    embedding_cache_dir: Path = BASE_DIR.parent / "model_cache"
    retrieval_top_k: int = Field(default=5, ge=1, le=20, strict=True)
    default_generation_model_id: str | None = None

    @model_validator(mode="after")
    def validate_chunking(self) -> Self:
        validate_chunk_settings(self.rag_chunk_size, self.rag_chunk_overlap)
        return self


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_list(name: str, default: list[str]) -> list[str]:
    value = os.getenv(name)
    if value is None:
        return default
    return [item.strip() for item in value.split(",") if item.strip()]


def _get_path(name: str, default: Path | None) -> Path | None:
    value = os.getenv(name)
    if not value:
        return default

    path = Path(value)
    if path.is_absolute():
        return path
    return BASE_DIR / path


def _get_optional(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else None


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    return int(value)


@lru_cache
def get_settings() -> Settings:
    return Settings(
        app_name=os.getenv("APP_NAME", "KQ Customer Support Chatbot API"),
        app_version=os.getenv("APP_VERSION", "1.0.0"),
        environment=os.getenv("ENVIRONMENT", "development"),
        debug=_get_bool("DEBUG", True),
        api_prefix=os.getenv("API_PREFIX", "/api/v1"),
        cors_origins=_get_list("CORS_ORIGINS", ["http://localhost:5173", "http://localhost:3000"]),
        firestore_project_id=_get_optional("FIRESTORE_PROJECT_ID"),
        firebase_credentials_path=_get_path("FIREBASE_CREDENTIALS_PATH", None),
        raw_data_dir=_get_path("RAW_DATA_DIR", BASE_DIR / "data" / "raw"),
        processed_data_dir=_get_path("PROCESSED_DATA_DIR", BASE_DIR / "data" / "processed"),
        chroma_persist_dir=_get_path("CHROMA_PERSIST_DIR", BASE_DIR.parent / "chroma_db"),
        rag_chunk_size=_get_int("RAG_CHUNK_SIZE", DEFAULT_CHUNK_SIZE),
        rag_chunk_overlap=_get_int("RAG_CHUNK_OVERLAP", DEFAULT_CHUNK_OVERLAP),
        embedding_model_name=_get_optional("EMBEDDING_MODEL_NAME"),
        embedding_model_revision=_get_optional("EMBEDDING_MODEL_REVISION"),
        embedding_batch_size=_get_int("EMBEDDING_BATCH_SIZE", 4),
        embedding_cache_dir=_get_path("EMBEDDING_CACHE_DIR", BASE_DIR.parent / "model_cache"),
        retrieval_top_k=_get_int("RETRIEVAL_TOP_K", 5),
        default_generation_model_id=_get_optional("DEFAULT_GENERATION_MODEL_ID"),
    )
