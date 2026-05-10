"""Single typed Settings object loaded from .env at startup.

Never scatter `os.getenv` calls — every config value lives here and is
validated at boot. See CLAUDE.md ("Configuration via Pydantic Settings").
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Tier(StrEnum):
    """Performance vs depth trade-off, set per-run via the CLI flag.

    Wall-time figures are measured against the v9.x five-company batch
    (BNP / Carrefour / L'Oréal / Mistral / Veolia, standard tier mean).

    fast      — web_search disabled in generation; polish + attribution
                check skipped; mistral-medium replaces mistral-large for
                the enrichment draft. Cuts ~50-60s versus standard at the
                cost of less polished prose. Estimated ~150s; benchmarked
                in Phase 3d.
    standard  — full pipeline (default). web_search budget 2 in generation,
                mistral-large for enrichment, polish + attribution always
                run. Measured wall time: ~213s mean across the v9.2 batch.
    max       — standard plus deeper verification: web_search budget 4 in
                generation, deeper Tavily verify pass per top candidate.
                Trades ~30-60s extra wall time for higher claim grounding
                density. Benchmarked in Phase 3d.
    """

    FAST = "fast"
    STANDARD = "standard"
    MAX = "max"

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

    candidates_to_generate: int = Field(
        default=8,
        ge=3,
        le=20,
        description=(
            "Number of candidate use cases the generator drafts before "
            "the score → top-3 filter. Was 12 through v9.2; cut to 8 in "
            "v9.3 after trace analysis showed the bottom 4 of 12 never "
            "made the top-3 (rejection appendix consistently kept the "
            "stronger near-misses). N=8 saves ~10-15s on generate + "
            "score with no measurable top-3 quality loss. N=5 was tested "
            "in Phase 3a — see docs/benchmarks/n_comparison.md."
        ),
    )
    top_k_precedents: int = Field(default=8, ge=3, le=20)
    research_confidence_threshold: float = Field(default=0.5, ge=0.0, le=1.0)
    meta_eval_confidence_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
    diversity_threshold: float = Field(
        default=0.92,
        ge=0.0,
        le=1.0,
        description=(
            "Avg pairwise candidate-cosine ABOVE this triggers a diversity "
            "regen of the 12 candidates. Calibrated against observed corpus "
            "behavior: random pairs in our corpus cluster at mean=0.78, std=0.03. "
            "Candidate descriptions written by the same enrichment LLM in the "
            "same voice cluster even tighter (~0.83-0.87 baseline). Threshold of "
            "0.85 fired regen on every run with negligible improvement (e.g. "
            "0.866→0.849); 0.92 fires only when candidates are genuinely "
            "repetitive. Saves ~30-40s per run."
        ),
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

    # ---- Performance tier --------------------------------------------------

    tier: Tier = Tier.STANDARD


# Module-level singleton, loaded once at process start.
settings = Settings()
