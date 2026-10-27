"""Application configuration loaded from environment variables / .env."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Sentinel RCA"
    environment: str = "development"

    # Storage
    database_url: str = "sqlite:///./data/sentinel.db"
    redis_url: str = ""  # empty -> in-process cache fallback
    qdrant_url: str = ""  # e.g. http://localhost:6333 ; empty -> embedded Qdrant
    qdrant_path: str = "./data/qdrant"  # ":memory:" for ephemeral embedded mode
    qdrant_api_key: str = ""
    qdrant_collection: str = "sentinel_incidents"

    # Embeddings: "auto" | "hash" | "sentence-transformers"
    embedding_provider: str = "auto"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_dim: int = 384

    # LLM (optional)
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    llm_timeout_seconds: float = 30.0
    auto_llm_narrative: bool = False

    # Detection defaults
    window_seconds: int = 60
    z_threshold: float = 3.5
    min_anomaly_count: int = 3
    min_baseline_windows: int = 8
    correlation_gap_seconds: int = 300
    analysis_lookback_hours: int = 24
    max_evidence_per_anomaly: int = 50

    # Ingestion
    max_upload_mb: int = 50
    max_batch_size: int = 10000

    # Demo
    seed_sample_data: bool = True

    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8080"]
    )

    @property
    def llm_enabled(self) -> bool:
        return bool(self.groq_api_key.strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()
