"""Single typed Settings object loaded from .env at startup.

Never scatter `os.getenv` calls — every config value lives here and is
validated at boot. See CLAUDE.md ("Configuration via Pydantic Settings").
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- API keys ----------------------------------------------------------

    mistral_api_key: str = Field(
        default="",
        description="Mistral AI Studio API key — required for all LLM calls and embeddings.",
    )
    tavily_api_key: str = Field(
        default="",
        description="Tavily Search API key — required for news search and per-candidate verification.",
    )

    # ---- Mistral models per pipeline step (overridable from env) ----------

    mistral_research_model: str = "mistral-medium-2604"
    mistral_generation_model: str = "mistral-medium-2604"
    mistral_scoring_model: str = "mistral-small-2603"
    mistral_verification_model: str = "mistral-small-2603"
    mistral_enrichment_model: str = "mistral-large-2512"
    mistral_meta_eval_model: str = "mistral-medium-2604"
    mistral_embedding_model: str = "mistral-embed"

    # ---- Pipeline parameters ----------------------------------------------

    candidates_to_generate: int = Field(default=12, ge=3, le=20)
    top_k_precedents: int = Field(default=8, ge=3, le=20)
    research_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    meta_eval_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    diversity_threshold: float = Field(
        default=0.85,
        ge=0.0,
        le=1.0,
        description="Pairwise cosine similarity above this triggers regeneration of candidate batch.",
    )

    # ---- Data layer --------------------------------------------------------

    sqlite_path: Path = PROJECT_ROOT / "data" / "genai_usecases.db"
    data_raw_dir: Path = PROJECT_ROOT / "data" / "raw"

    # ---- Cache TTLs (seconds) ---------------------------------------------

    cache_ttl_wikipedia_seconds: int = 30 * 24 * 3600  # 30 days
    cache_ttl_news_seconds: int = 24 * 3600  # 24 hours
    cache_ttl_jobs_seconds: int = 48 * 3600  # 48 hours
    cache_ttl_existing_initiatives_seconds: int = 7 * 24 * 3600  # 7 days
    cache_ttl_per_candidate_verify_seconds: int = 7 * 24 * 3600  # 7 days

    # ---- HTTP behavior -----------------------------------------------------

    user_agent: str = (
        "GenAIUseCaseGenerator/0.1 (https://github.com/alidor4702/genai-usecase-generator)"
    )
    http_timeout_seconds: float = 15.0

    # ---- Tavily settings ---------------------------------------------------

    tavily_max_results: int = 5
    tavily_deep_read_top_n: int = 2

    # ---- Lightpanda CDP fallback ------------------------------------------

    lightpanda_cdp_url: str | None = None  # e.g. "ws://localhost:9222" if running


# Module-level singleton, loaded once at process start.
settings = Settings()
