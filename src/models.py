"""Pydantic models for the GenAI Use Case Generator pipeline.

Every activity input and output is a typed Pydantic model. There is no point in
the pipeline where prose is parsed downstream — every handoff is type-safe.
See docs/architecture.md and docs/methodology.md for the full design.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from src.config import Tier

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


class Novelty(StrEnum):
    """Whether a candidate is adapted from existing precedents or a novel direction.

    Per the methodology, ≥3 of the generated candidates MUST be `novel_direction` — not
    direct adaptations of any single precedent. This field carries that meta-signal
    in a structured way rather than embedded in the description text.
    """

    ADAPTED_FROM_PRECEDENT = "adapted_from_precedent"
    NOVEL_DIRECTION = "novel_direction"


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
    """Top-level input to GenAIUseCaseWorkflow.

    Le Chat auto-renders this schema as the workflow's entry form on
    the assistant. We surface FOUR fields in the form (company, focus
    area, tier, weights). `research_depth` is a server-side default
    held on the model for CLI / web / API but explicitly stripped
    from the JSON schema in `model_json_schema()` below — Le Chat
    users get medium depth (the right default for ~95% of runs);
    power users who want to tune it use the standalone web app or
    the CLI's --depth flag.
    """

    company_name: str = Field(
        min_length=1,
        max_length=200,
        title="Company",
        description=(
            "The company you want GenAI use cases for. "
            "Examples: Carrefour, Mistral AI, L'Oréal, Veolia, BNP Paribas. "
            "Any public company by legal or trade name works."
        ),
    )
    focus_area: FocusArea = Field(
        default=FocusArea.GENERAL,
        title="Focus area",
        description=(
            "Bias the use cases toward a surface. "
            "General — balanced, the right pick most of the time. "
            "Operations — supply chain, cost, efficiency. "
            "Customer — CX, personalisation. "
            "Sustainability — ESG, compliance."
        ),
    )
    tier: Tier = Field(
        default=Tier.STANDARD,
        title="Performance tier",
        description=(
            "Speed vs depth trade-off. "
            "Fast (~125s) uses Mistral Medium for the prose, skips polish + attribution. "
            "Standard (~215s, default) uses Mistral Large 3 with full guardrails. "
            "Max (~225s) bumps web_search to 4, deep-read to top 5, judge to T=0.05, "
            "rescue cap to 18 — pick this when claim density matters more than time."
        ),
    )
    weights: CriteriaWeights = Field(
        default_factory=CriteriaWeights,
        title="Criteria weights (advanced — leave at 0.2 each unless you have a strong opinion)",
        description=(
            "Per-criterion weight in the scoring step. Defaults to 0.2 each "
            "(equal across relevance, iconic potential, estimated impact, "
            "feasibility, Mistral suitability). Tune only if you want one "
            "criterion to dominate — for example, set Mistral suitability "
            "higher to bias toward use cases that lean into Mistral's "
            "distinctive strengths."
        ),
    )

    # Server-side default — kept on the model for CLI / web / API but
    # stripped from the JSON schema below so Le Chat's auto-form
    # doesn't render it.
    research_depth: ResearchDepth = Field(default=ResearchDepth.MEDIUM)

    @classmethod
    def model_json_schema(cls, *args: Any, **kwargs: Any) -> dict[str, Any]:  # type: ignore[override]
        schema = super().model_json_schema(*args, **kwargs)
        # Hide research_depth from the Le Chat auto-form. Pydantic's
        # `exclude=True` only drops the field from serialization, not
        # from the schema; we have to manually rewrite the schema.
        if "properties" in schema and "research_depth" in schema["properties"]:
            schema["properties"].pop("research_depth")
        if "required" in schema and "research_depth" in schema["required"]:
            schema["required"].remove("research_depth")
        if "$defs" in schema and "ResearchDepth" in schema["$defs"]:
            # Remove the orphan ResearchDepth enum definition too if
            # nothing else references it. Keep it if any other field
            # still does.
            other_refs = any(
                "ResearchDepth" in str(v) for k, v in schema.get("properties", {}).items()
            )
            if not other_refs:
                schema["$defs"].pop("ResearchDepth", None)
        return schema


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
    # Open-ended catch-all: anything specific to this company that didn't
    # cleanly fit a structured field (e.g. Carrefour Media as a retail-media
    # priority, the Couche-Tard merger talks). Generation reads this so the
    # schema doesn't bottleneck the model on under-fitted facts.
    free_text_notes: str | None = None


class ResearchBundle(BaseModel):
    """Raw outputs of the parallel research sub-tasks before synthesis."""

    wikipedia: WikipediaFacts
    news: list[NewsItem] = Field(default_factory=list)
    jobs: JobsSignal | None = None
    existing_initiatives: list[ExistingInitiative] = Field(default_factory=list)
    verified_match: VerifiedCompanyMatch
    depth_used: ResearchDepth


# ----------------------------------------------------------------------------
# Evidence ledger — every external source the pipeline reads is recorded here
# so downstream steps (enrichment, fact-check, meta-eval) can pin claims to
# specific source content rather than to a flattened CompanyContext.
# ----------------------------------------------------------------------------


class EvidenceKind(StrEnum):
    WIKIPEDIA = "wikipedia"
    NEWS = "news"
    TAVILY = "tavily"
    PRECEDENT = "precedent"
    JOBS = "jobs"
    EXISTING_INITIATIVE = "existing_initiative"
    COMPANY_VERIFICATION = "company_verification"
    GAP_FILL = "gap_fill"
    GENERATION_TOOL = "generation_tool"
    PER_CANDIDATE_VERIFICATION = "per_candidate_verification"
    CLAIM_VERIFICATION = "claim_verification"  # post-meta-eval rescue search


class EvidenceItem(BaseModel):
    id: str  # short stable hash (e.g. "ev-a1b2c3d4ef") — used in claim citations
    source_kind: EvidenceKind
    url: str | None = None
    title: str
    content: str  # full deep-read text where available; snippet otherwise
    fetched_at_step: str  # research | gap_fill | retrieve | generation_tool | verification
    confidence: Literal["high", "medium", "low"] = "medium"


class EvidenceLedger(BaseModel):
    """Append-only ledger of every external source the pipeline read.

    Threaded as typed input/output through the research → retrieve → generate
    → verify chain. Meta-eval reads it to verify claims against source content.
    """

    entries: list[EvidenceItem] = Field(default_factory=list)

    def add(self, item: EvidenceItem) -> bool:
        """Add an entry, deduping by id. Returns True if added, False if duplicate."""
        if any(e.id == item.id for e in self.entries):
            return False
        self.entries.append(item)
        return True

    def by_id(self, evidence_id: str) -> EvidenceItem | None:
        for e in self.entries:
            if e.id == evidence_id:
                return e
        return None

    def ids(self) -> list[str]:
        return [e.id for e in self.entries]

    def of_kind(self, kind: EvidenceKind) -> list[EvidenceItem]:
        return [e for e in self.entries if e.source_kind == kind]


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
    source: (
        Literal["evidently", "google_cloud_1001", "google_cloud_blueprints", "google_cloud_1302"]
        | None
    ) = None
    embedding: list[float] | None = None  # 1024-d mistral-embed; None until indexed


class RetrievedPrecedents(BaseModel):
    items: list[Precedent]
    similarity_scores: list[float] = Field(default_factory=list)
    used_mmr: bool = False


# ----------------------------------------------------------------------------
# Candidates and scoring
# ----------------------------------------------------------------------------


class Candidate(BaseModel):
    """One of the candidate use cases produced by the generator
    (`candidates_to_generate` per run, 8 by default — v9.3+ default; was 12)."""

    id: str  # short slug, e.g. "kyc-doc-review"
    title: str
    description: str
    why_this_company: str
    estimated_impact_summary: str
    suggested_mistral_products: list[str] = Field(default_factory=list)
    novelty: Novelty = Novelty.ADAPTED_FROM_PRECEDENT
    inspired_by: list[str] = Field(default_factory=list)  # precedent IDs
    grounded_in: list[str] = Field(default_factory=list)  # company-context field paths
    # EvidenceLedger entry IDs (e.g. "ev-a1b2c3d4") that the generator pulled
    # via the web_search tool and used to anchor concrete claims.
    evidence_ids: list[str] = Field(default_factory=list)
    # Generator self-marks a sibling candidate when this one shares
    # workflow / data asset / user persona / value chain stage with another
    # candidate in the same batch. Top-3 selection swaps a lower-scored
    # near_dup out for the next non-linked candidate from the appendix.
    near_dup_of: str | None = None


class CandidateBatch(BaseModel):
    """The candidates returned by the generator (configurable count;
    8 by default — see `Settings.candidates_to_generate`)."""

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


class SupportingSnippet(BaseModel):
    """A claim-supporting excerpt the verifier extracted while doing duplicate
    detection. Flowed into the EvidenceLedger so enrichment + meta-eval see
    these per-candidate Tavily fetches as grounding (not just dup-check input).
    """

    quote: str
    url: str
    title: str | None = None


class VerificationResult(BaseModel):
    candidate_id: str
    verdict: VerificationVerdict
    rationale: str
    sources_consulted: list[str] = Field(default_factory=list)
    supporting_snippets: list[SupportingSnippet] = Field(default_factory=list)


class VerificationBatch(BaseModel):
    """Verification outcomes for the top-k scored candidates."""

    results: list[VerificationResult]
    promoted_near_misses: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------------------
# Selection & enrichment — the customer-ready output
# ----------------------------------------------------------------------------


class TimeToValueBasis(StrEnum):
    """Why we trust the time_to_value estimate.

    `precedent` — anchored to ≥1 cited peer deployment in the corpus; the
        figure should match (or near-match) something in that precedent's
        content. The fact-checker treats this as a substantive claim.
    `ballpark_assumption` — no comparable precedent exists. The model is
        making an honest engineering ballpark based on the candidate's
        complexity tier + scope. Customer should treat as estimate, not fact.
        The fact-checker SKIPS these in the claims list (they're explicitly
        flagged as not-a-claim, not fabrication).
    `unknown` — model cannot reasonably estimate at all (rare).
    """

    PRECEDENT = "precedent"
    BALLPARK_ASSUMPTION = "ballpark_assumption"
    UNKNOWN = "unknown"


class TimeToValue(BaseModel):
    estimate: str  # e.g. "8-16 weeks" or "unknown"
    anchored_to: list[str] = Field(default_factory=list)  # precedent IDs
    basis: TimeToValueBasis = TimeToValueBasis.UNKNOWN
    rationale: str | None = None  # one-line WHY for ballpark_assumption


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
    evidence_ids: list[str] = Field(default_factory=list)  # carries forward from Candidate
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
    # source_kind / source_url carry where the meta-eval said the claim is
    # supported from. The final-render-gate judge uses them to verify the
    # source actually backs the claim (vs. just containing related entities).
    # source_kind values mirror the META_EVALUATION_SYSTEM rubric:
    #   "evidence:<ev-id>" | "precedent:<id>" | "company_context.<field>" | None
    source_kind: str | None = None
    source_url: str | None = None
    # Set by the post-meta-eval web-verify rescue layer when a claim was
    # promoted from passed=False → passed=True via a fresh Tavily search.
    # "verified"     — source was on the curated allowlist (company-official,
    #                  major business press, government/EU regulator).
    # "corroborated" — non-allowlist source but entity+number anchor matched.
    # None           — passed via the normal evidence-pool fact-check, no
    #                  rescue happened.
    rescue_tier: Literal["verified", "corroborated"] | None = None
    rescue_url: str | None = None
    # Set by the final-render-gate judge when it inspected the (claim, source)
    # pair and decided the source does NOT support the claim. The claim flips
    # back to passed=False; this field carries the judge's reason for the
    # transparency block.
    judge_rejected: bool = False
    judge_reason: str | None = None
    # Set by Step 7e (final qualitative replacement) when the prose was
    # rewritten so this specific claim is no longer asserted in the
    # customer-facing text. These claims are excluded from the fact-check
    # pass-rate denominator (the prose doesn't make the claim any more, so
    # whether it's "supported" is moot) but stay visible in the transparency
    # block tagged "[rewritten qualitatively]" so the audit trail is intact.
    qualified_out: bool = False
    # Set by Step 7d (source-judge) when the snippet contradicted the
    # claim's specific value but provided a same-fact correction. Judge
    # returns the corrected_value; pipeline rewrites the prose inline
    # using that value + attaches the source URL. Claim flips to
    # passed=True and renders with a `[corrected ↗ original→corrected]`
    # chip in the transparency block. Restricted to numeric / rank /
    # temporal facts — entity contradictions stay unsupported because
    # silent entity-substitution would break downstream prose semantics.
    corrected: bool = False
    original_value: str | None = None
    corrected_value: str | None = None


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
        # Exclude `qualified_out` claims from the denominator: those are
        # claims that the original draft made but Step 7e rewrote out of
        # the prose qualitatively. Counting them as "unsupported" against
        # a rate the report no longer asserts would under-report the
        # actual reliability of the rendered text.
        in_scope = [f for f in self.fact_check if not f.qualified_out]
        if not in_scope:
            return 1.0
        return sum(1 for f in in_scope if f.passed) / len(in_scope)


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


# `RefusalReport` was an earlier sketch for a structured refusal payload.
# In the current pipeline, refusals are signalled by
# `PipelineResult.refused=True` + `refusal_reason: str`, and the workflow
# class emits a `ChatAssistantWorkflowOutput` with a plain-text message
# directly. No callers ever constructed `RefusalReport`, so it's removed
# to avoid dead-code drift.
