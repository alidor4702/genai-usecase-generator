"""Pydantic models for the GenAI Use Case Generator pipeline.

Every activity input and output is a typed Pydantic model. There is no point in
the pipeline where prose is parsed downstream — every handoff is type-safe.
See docs/architecture.md and docs/methodology.md for the full design.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, model_validator

# ----------------------------------------------------------------------------
# Top-level enums
# ----------------------------------------------------------------------------


class FocusArea(StrEnum):
    GENERAL = "general"
    OPERATIONS = "operations"
    CUSTOMER = "customer"
    SUSTAINABILITY = "sustainability"


class ResearchDepth(StrEnum):
    """Scales how many parallel research sub-tasks run.

    low    : Wikipedia + verified-companies + existing-initiatives
    medium : low + recent news (deep-read)
    high   : medium + job postings (httpx + Playwright/Lightpanda fallback)
    """

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ImpactTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CostTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    UNKNOWN = "unknown"


class ComplexityTier(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class VerificationVerdict(StrEnum):
    PASS = "pass"
    PARTIAL_OVERLAP = "partial_overlap"
    CONFIRMED_EXISTING = "confirmed_existing"


class BlueprintPattern(StrEnum):
    RAG = "rag"
    AGENT_WITH_TOOLS = "agent_with_tools"
    DOCUMENT_AI_PIPELINE = "document_ai_pipeline"
    FINE_TUNED_DOMAIN = "fine_tuned_domain"
    HYBRID_RETRIEVAL = "hybrid_retrieval"


# ----------------------------------------------------------------------------
# Input / configuration
# ----------------------------------------------------------------------------


class CriteriaWeights(BaseModel):
    """User-configurable weights for the five scoring dimensions.

    Defaults to 0.2 each (equal). Weights are normalized to sum to 1.0 at use.
    """

    relevance: float = Field(default=0.2, ge=0.0, le=1.0)
    iconic_potential: float = Field(default=0.2, ge=0.0, le=1.0)
    estimated_impact: float = Field(default=0.2, ge=0.0, le=1.0)
    feasibility: float = Field(default=0.2, ge=0.0, le=1.0)
    mistral_suitability: float = Field(default=0.2, ge=0.0, le=1.0)

    def normalized(self) -> CriteriaWeights:
        total = (
            self.relevance
            + self.iconic_potential
            + self.estimated_impact
            + self.feasibility
            + self.mistral_suitability
        )
        if total <= 0:
            return CriteriaWeights()
        return CriteriaWeights(
            relevance=self.relevance / total,
            iconic_potential=self.iconic_potential / total,
            estimated_impact=self.estimated_impact / total,
            feasibility=self.feasibility / total,
            mistral_suitability=self.mistral_suitability / total,
        )


class WorkflowInput(BaseModel):
    """Top-level input to GenAIUseCaseWorkflow."""

    company_name: str = Field(min_length=1, max_length=200)
    focus_area: FocusArea = FocusArea.GENERAL
    weights: CriteriaWeights = Field(default_factory=CriteriaWeights)
    research_depth: ResearchDepth = ResearchDepth.MEDIUM


class WorkflowStatus(BaseModel):
    """Returned by the workflow's `get_status` query."""

    company: str | None = None
    current_step: str = "initialized"
    progress_percent: float = Field(default=0.0, ge=0.0, le=100.0)


# ----------------------------------------------------------------------------
# Research sub-task outputs
# ----------------------------------------------------------------------------


class WikipediaFacts(BaseModel):
    found: bool
    summary: str | None = None
    industry: str | None = None
    geography: str | None = None
    business_model: str | None = None
    founded_context: str | None = None
    wikidata_id: str | None = None


class NewsItem(BaseModel):
    title: str
    url: str
    snippet: str | None = None
    deep_content: str | None = None
    published_at: str | None = None  # ISO 8601 if known


class JobPostingSignal(BaseModel):
    role_title: str
    description: str
    source_url: str | None = None


class JobsSignal(BaseModel):
    """Aggregated AI/ML hiring direction observed across postings."""

    summary: str
    notable_postings: list[JobPostingSignal] = Field(default_factory=list)
    used_fallback: bool = False  # True if Playwright/Lightpanda was used


class ExistingInitiativeSource(StrEnum):
    OFFICIAL_ANNOUNCEMENT = "official_announcement"
    NEWS = "news"
    INDUSTRY_REPORTING = "industry_reporting"
    ENGINEERING_BLOG = "engineering_blog"
    PRECEDENT_CORPUS = "precedent_corpus"


class ExistingInitiative(BaseModel):
    description: str
    source: ExistingInitiativeSource
    source_url: str | None = None
    confidence: Literal["high", "medium", "low"] = "medium"


class VerifiedCompanyMatch(BaseModel):
    """Output of the verified-companies index lookup (rapidfuzz)."""

    matched: bool
    name: str | None = None
    aliases: list[str] = Field(default_factory=list)
    industry: str | None = None
    country: str | None = None
    wikidata_id: str | None = None
    score: float = Field(default=0.0, ge=0.0, le=100.0)


# ----------------------------------------------------------------------------
# CompanyContext — the typed output of the research synthesis step
# ----------------------------------------------------------------------------


class CompanyIdentity(BaseModel):
    name: str
    legal_name: str | None = None


class CompanyClassification(BaseModel):
    industry: str
    sub_industries: list[str] = Field(default_factory=list)
    geography: str | None = None
    operating_regions: list[str] = Field(default_factory=list)


class CompanyScale(BaseModel):
    size_tier: Literal["startup", "scaleup", "enterprise", "unknown"] = "unknown"
    public_or_private: Literal["public", "private", "unknown"] = "unknown"


class CompanyBusiness(BaseModel):
    business_model: str | None = None
    primary_customers: Literal["B2B", "B2C", "B2G", "mixed", "unknown"] = "unknown"
    key_products_or_services: list[str] = Field(default_factory=list)


class CompanyDataAndTech(BaseModel):
    likely_data_assets: list[str] = Field(default_factory=list)
    known_tech_maturity: Literal["high", "medium", "low", "unknown"] = "unknown"


class CompanyStrategicContext(BaseModel):
    stated_priorities: list[str] = Field(default_factory=list)
    recent_strategic_moves: list[str] = Field(default_factory=list)


class CompanyConstraints(BaseModel):
    regulatory_context: list[str] = Field(default_factory=list)
    data_sovereignty_concerns: bool = False


class CompanyMeta(BaseModel):
    research_confidence: float = Field(ge=0.0, le=1.0)
    research_sources: list[str] = Field(default_factory=list)
    is_verified: bool = False


class CompanyContext(BaseModel):
    """The synthesized, typed picture of a company that downstream steps consume."""

    identity: CompanyIdentity
    classification: CompanyClassification
    scale: CompanyScale = Field(default_factory=CompanyScale)
    business: CompanyBusiness = Field(default_factory=CompanyBusiness)
    data_and_tech: CompanyDataAndTech = Field(default_factory=CompanyDataAndTech)
    strategic_context: CompanyStrategicContext = Field(default_factory=CompanyStrategicContext)
    existing_ai_initiatives: list[ExistingInitiative] = Field(default_factory=list)
    constraints: CompanyConstraints = Field(default_factory=CompanyConstraints)
    meta: CompanyMeta


class ResearchBundle(BaseModel):
    """Raw outputs of the parallel research sub-tasks before synthesis."""

    wikipedia: WikipediaFacts
    news: list[NewsItem] = Field(default_factory=list)
    jobs: JobsSignal | None = None
    existing_initiatives: list[ExistingInitiative] = Field(default_factory=list)
    verified_match: VerifiedCompanyMatch
    depth_used: ResearchDepth


# ----------------------------------------------------------------------------
# Precedent corpus
# ----------------------------------------------------------------------------


class Precedent(BaseModel):
    """One curated entry in the peer-deployment corpus."""

    id: str
    company: str
    industry: str
    title: str
    description: str
    outcome: str | None = None
    deep_content: str | None = None  # full article body where available
    source_url: str | None = None
    source: Literal["evidently", "google_cloud_1001", "google_cloud_blueprints"] | None = None
    embedding: list[float] | None = None  # 1024-d mistral-embed; None until indexed


class RetrievedPrecedents(BaseModel):
    items: list[Precedent]
    similarity_scores: list[float] = Field(default_factory=list)
    used_mmr: bool = False


# ----------------------------------------------------------------------------
# Candidates and scoring
# ----------------------------------------------------------------------------


class Candidate(BaseModel):
    """One of the 12 use cases produced by the generator."""

    id: str  # short slug, e.g. "kyc-doc-review"
    title: str
    description: str
    why_this_company: str
    estimated_impact_summary: str
    suggested_mistral_products: list[str] = Field(default_factory=list)
    inspired_by: list[str] = Field(default_factory=list)  # precedent IDs
    grounded_in: list[str] = Field(default_factory=list)  # company-context field paths


class CandidateBatch(BaseModel):
    """The 12 candidates returned by the generator."""

    candidates: list[Candidate]
    diversity_score: float = Field(default=0.0, ge=0.0, le=1.0)
    regenerated_for_diversity: bool = False


class CriterionScore(BaseModel):
    score: int = Field(ge=1, le=10)
    rationale: str


class ScoredCandidate(BaseModel):
    candidate: Candidate
    relevance: CriterionScore
    iconic_potential: CriterionScore
    estimated_impact: CriterionScore
    feasibility: CriterionScore
    mistral_suitability: CriterionScore
    aggregate_score: float = 0.0  # weighted average computed by `aggregate_with_weights`

    def aggregate_with_weights(self, w: CriteriaWeights) -> float:
        wn = w.normalized()
        return (
            wn.relevance * self.relevance.score
            + wn.iconic_potential * self.iconic_potential.score
            + wn.estimated_impact * self.estimated_impact.score
            + wn.feasibility * self.feasibility.score
            + wn.mistral_suitability * self.mistral_suitability.score
        )


class ScoredBatch(BaseModel):
    """Output of the scoring activity."""

    scored: list[ScoredCandidate]
    weights_used: CriteriaWeights
    self_consistency_passes: int = Field(default=2, ge=1)


# ----------------------------------------------------------------------------
# Per-candidate verification
# ----------------------------------------------------------------------------


class VerificationResult(BaseModel):
    candidate_id: str
    verdict: VerificationVerdict
    rationale: str
    sources_consulted: list[str] = Field(default_factory=list)


class VerificationBatch(BaseModel):
    """Verification outcomes for the top-k scored candidates."""

    results: list[VerificationResult]
    promoted_near_misses: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Selection & enrichment — the customer-ready output
# ----------------------------------------------------------------------------


class TimeToValue(BaseModel):
    estimate: str  # e.g. "8-16 weeks" or "unknown"
    anchored_to: list[str] = Field(default_factory=list)  # precedent IDs


class EnrichedUseCase(BaseModel):
    id: str
    title: str
    description: str
    why_this_company: str
    example_input: str
    example_output: str
    suggested_mistral_products: list[str]
    blueprint_pattern: BlueprintPattern
    blueprint_mermaid: str
    time_to_value: TimeToValue
    operating_cost_tier: CostTier
    impact_tier: ImpactTier
    complexity_tier: ComplexityTier
    top_implementation_risk: str
    inspired_by: list[str] = Field(default_factory=list)
    grounded_in: list[str] = Field(default_factory=list)
    builds_on_existing: bool = False  # True if verifier returned partial_overlap
    builds_on_note: str | None = None


class RejectedCandidate(BaseModel):
    title: str
    one_line_reason: str


# ----------------------------------------------------------------------------
# Quality signals & meta-evaluation
# ----------------------------------------------------------------------------


class FactCheckEntry(BaseModel):
    claim: str
    use_case_id: str
    passed: bool
    rationale: str | None = None


class QualitySignals(BaseModel):
    diversity: float = Field(ge=0.0, le=1.0)
    specificity_per_use_case: list[float] = Field(default_factory=list)
    mistral_product_diversity: int = 0
    time_to_value_spread: list[str] = Field(default_factory=list)
    cost_tier_spread: list[CostTier] = Field(default_factory=list)
    source_coverage_per_use_case: list[list[str]] = Field(default_factory=list)
    risks_per_use_case: list[str] = Field(default_factory=list)
    fact_check: list[FactCheckEntry] = Field(default_factory=list)

    @property
    def fact_check_pass_rate(self) -> float:
        if not self.fact_check:
            return 1.0
        return sum(1 for f in self.fact_check if f.passed) / len(self.fact_check)


class MetaEvalReview(BaseModel):
    confidence: float = Field(ge=0.0, le=1.0)
    sales_engineer_ready: bool
    weakest_use_case_id: str | None = None
    weakness_reason: str | None = None
    cross_cutting_concern: str | None = None
    duplicate_flag: str | None = None  # if any proposal still duplicates an existing initiative


# ----------------------------------------------------------------------------
# Final report
# ----------------------------------------------------------------------------


class Report(BaseModel):
    """The complete output rendered to the user."""

    company: CompanyContext
    weights_used: CriteriaWeights
    focus_area: FocusArea
    research_depth: ResearchDepth
    top_use_cases: list[EnrichedUseCase]
    rejected_appendix: list[RejectedCandidate] = Field(default_factory=list)
    quality: QualitySignals
    meta_review: MetaEvalReview | None = None
    intro_text: str  # one-paragraph cover summary

    @model_validator(mode="after")
    def _check_three_use_cases(self) -> Report:
        if len(self.top_use_cases) != 3:
            raise ValueError(
                f"Report must contain exactly 3 use cases, got {len(self.top_use_cases)}"
            )
        return self


class RefusalReport(BaseModel):
    """Returned in place of Report when research signal is too sparse."""

    company_name: str
    reason: str
    signals_attempted: list[str]
    suggested_clarifications: list[str]
