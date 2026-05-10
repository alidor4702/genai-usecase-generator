# Methodology

## What this system does

This system takes a company name as input and produces a structured report of three GenAI use cases that are, for that specific company, relevant, iconic, and high-impact. It is intended as the kind of artifact a Mistral Proto Team applied AI engineer would produce in the early scoping phase of a customer engagement.

## Design philosophy

### Methodology first, output second

The core question is not "how do we generate plausible AI ideas" but "what makes a GenAI use case actually relevant, iconic, and high-impact for a specific company?" Every architectural decision in the system is in service of producing outputs that satisfy that definition. The methodology, encoded in code as `criteria.py`, is the source of truth.

### Grounding over generation

LLMs left to their own devices will produce plausible-sounding but generic GenAI proposals — predictive maintenance, customer service chatbots, document summarization. These are correct in the loose sense but useless to a customer because they could apply to any company. The system avoids this by:

1. Researching the specific company before generating, with multiple parallel signal sources (Wikipedia, recent news, job postings, peer precedents, existing AI initiatives at the company).
2. Retrieving real shipped deployments from peer companies and injecting them into the generation prompt as in-context examples.
3. Enforcing through prompts and scoring that every use case must cite what is specific to this company — its data assets, its stated priorities, its regulatory context — rather than reasoning at the level of its industry.

### Differentiation: proven-elsewhere vs already-done-here

A use case has two distinct relationships with prior art:

- **Proven elsewhere** at peer companies — this is a *positive* signal. It de-risks the use case (precedent exists, the pattern works) without diminishing its value for the target company.
- **Already done here** by the target company itself — this is a *hard disqualifier*. Recommending what a customer is already doing is the single most embarrassing failure mode possible. The system explicitly checks for this in four independent layers (broad research lookup, scorer hard-gate, per-candidate targeted verification, meta-evaluator final pass).

The strongest position for any candidate use case is: *proven at peer companies, not yet implemented at this specific company.* This combination signals both feasibility and opportunity. It is the framing the system actively rewards.

### Grounded but not derivative

Precedents are evidence of what's feasible, not templates to copy. The generator is instructed that at least 3 of its candidates (8 by default; configurable via `candidates_to_generate`) must be novel directions — extensions, combinations, or original framings that aren't direct adaptations of any single precedent. This protects against the failure mode where the system becomes a precedent paraphraser, regurgitating peer patterns without adapting to the target company's unique position. The strongest candidates often combine elements from multiple precedents in ways specific to the target company's data, brand, or strategic priorities.

### Refusal as a feature

Where the system has insufficient grounding to confidently generate output (an unknown company, sparse research signal), it refuses gracefully and explains what would help, rather than fabricating plausible-looking fiction. This is treated as a quality property, not a limitation.

### Structured outputs at every step

Every LLM call in the pipeline produces Pydantic-validated JSON, not free-form text. This compounds: when the generator outputs structured candidates with explicit `why_this_company`, `inspired_by`, and `grounded_in` fields, the scorer sees that structure and can evaluate the why-statement specifically rather than scoring an unstructured blob. Each step's output becomes the next step's well-typed input. There is no point in the pipeline where prose is parsed downstream — every handoff is type-safe.

## The five criteria

Every candidate use case is scored on five dimensions. These dimensions are the operational definition of "relevant, iconic, and high-impact for a specific company using Mistral tools."

Each criterion includes both positive and negative examples in the prompt. The negative anchor — "here is what bad looks like" — is more powerful than the positive one because it gives the model a clear signal of what to avoid.

### Relevance

A use case is relevant if it touches a core business workflow that the company runs at scale, the company has the data assets needed to make it work, and it addresses a known strategic priority or pain point.

- *Good*: Veolia operates at scale across thousands of municipal water networks. AI-assisted leak detection across the smart-meter network is relevant — it touches core water-utility operations, exploits an existing data asset (smart meter telemetry), and aligns with stated priorities around resource efficiency.
- *Bad*: An AI-powered HR assistant for Veolia. Possible? Yes. But it doesn't touch the company's core business and could equally be recommended to any large employer. Low relevance.

### Iconic potential

A use case is iconic if it would be visibly associated with this company specifically — not a generic AI-could-help-any-business idea — AND the company is not currently doing it or anything substantially similar. It exploits something distinctive: their brand, their data, their unique market position. A customer or employee should react with "this is so [Company]" if shown the use case.

- *Good*: For L'Oréal — an AI virtual try-on assistant trained on L'Oréal's multi-decade catalog of skin-tone and product data, embedded in their flagship retail experiences (assuming they haven't already deployed this).
- *Bad — generic*: For L'Oréal — a chatbot for customer service. Could be any consumer brand. Low iconic.
- *Bad — already done*: For L'Oréal — an AI personalized skincare recommendation engine, when L'Oréal has publicly deployed exactly this. Hard disqualifier; iconic score capped low regardless of other merits.

The "already-done-here" check is enforced as a hard gate within iconic scoring. The scorer is given the company's existing AI initiatives as context and substantially overlapping proposals score 1-2 on this dimension. A separate per-candidate verification activity (described in the architecture document) provides an additional precise check after selection.

### Estimated impact

A use case is high-impact if it has measurable financial impact (cost saved, revenue unlocked, time saved at scale) or clear strategic value (defensible moat, regulatory advantage, brand differentiation), large enough to justify GenAI's complexity and operating cost.

- *Good*: For BNP Paribas — an AI-powered KYC document review system that reduces onboarding time per corporate client from weeks to days across 5+ million clients. Quantifiable across a scale that justifies the build.
- *Bad*: A small efficiency improvement on an internal tool used by 20 employees. Real but too small to matter at the company's scale. Low impact.

Time-to-value and cost-tier estimates within this dimension are anchored to the precedent corpus — that is, "8-16 weeks based on similar deployments at peer companies" — and "unknown" is a valid output if no comparable precedent exists.

### Feasibility

A use case is feasible if it is shippable with current GenAI technology within a reasonable engagement timeline (weeks to months, not years), without requiring fundamental research breakthroughs. Feasibility considers data availability, technical maturity, regulatory clearability, and integration complexity.

- *Good*: A retrieval-augmented assistant over already-digitized policy documents — well-understood pattern, real precedents, ships in weeks.
- *Bad*: Real-time multi-agent autonomous decision-making across regulated financial transactions — possible in research, not realistic to ship in a customer engagement.

### Mistral suitability

A use case is Mistral-suitable if it leans into something Mistral does distinctively well — not just "an LLM does this," but specifically "Mistral is the right LLM provider here." Drivers include data sovereignty (EU-hosted, on-premise deployable), open-weight options (fine-tuning and self-hosting flexibility), multilingual capability (strong in European languages), competitive cost-quality tradeoffs, and customer alignment (European companies, regulated sectors, companies skeptical of US hyperscaler lock-in).

This dimension explicitly captures *why Mistral, not OpenAI or Anthropic or Google*. A use case that scores high on the other four but neutral on Mistral suitability is still a real use case, just not necessarily a Mistral-specific one.

### Configurable weights

By default, the five dimensions are equally weighted (20% each). Users may adjust weights to reflect specific customer priorities — for example, a customer with no strong vendor preference may set Mistral suitability weight to zero, scoring purely on the technical and business dimensions. Weights are exposed both in the conversational workflow (an advanced settings form built with `FormInput` + `NumberField`) and in the standalone web app.

The five dimensions themselves are fixed. Users cannot invent new criteria mid-flight, because that would unbound the scorer's prompt.

## Process — how a query becomes a report

The system is a seven-activity pipeline running inside a Mistral Workflows orchestrator. Each activity is a focused LLM call (or parallel set of calls) with a typed input and typed output. No activity carries forward state it doesn't need; each step receives exactly the inputs it requires.

A user submits a company name. The workflow optionally asks for a focus area and weight overrides via an interactive checkpoint, then:

1. **Research.** Multiple parallel sub-tasks fetch signal from different sources — structured facts (Wikipedia), recent news, job postings, peer precedents, existing AI initiatives at this company — and an LLM synthesis call aggregates them into a typed `CompanyContext`. A confidence score on this context gates the rest of the pipeline. The number of parallel sub-tasks scales with a depth toggle (low/medium/high) — 3 sub-tasks at low depth, 4 at medium, 5 at high — defaulting to medium. This follows Anthropic's principle of scaling effort to query complexity.

2. **Retrieve precedents.** The company context is embedded and used to retrieve the top-k most relevant entries from a curated precedent corpus of real shipped GenAI deployments. Top-k is typically 5-8. Entries from the target company itself are excluded from this set — they appear instead in the existing-initiatives context.

3. **Generate candidates.** A single LLM call, given the company context (including existing initiatives), the peer precedents, the focus area, and the criteria definitions, generates 8 candidate use cases as structured Pydantic output (default `candidates_to_generate=8`; cut from 12 in v9.3 after the rejection-appendix consistently kept the stronger near-misses as the bottom four). The prompt includes one or two hand-curated few-shot examples of high-quality outputs for other companies — this is the single highest-impact technique for output quality, anchoring the model on the exact style and structure we want. Negative examples ("here's what bad looks like") are included for each criterion. The prompt explicitly forbids proposals that substantially duplicate the company's existing AI initiatives and requires that at least 3 of the candidates be novel directions rather than direct adaptations of any single precedent.

4. **Score candidates.** A judge LLM call evaluates each candidate against the five criteria, producing per-dimension scores (1-10) and per-dimension rationales. The scorer is shown the existing-initiatives list and applies a hard penalty on iconic for substantial duplicates. For robustness, scoring is run with self-consistency — two passes at slightly different temperatures, with scores averaged. Self-consistency is applied only here because the scorer is the cheapest LLM call in the pipeline and the most quality-sensitive to noise. Weighted aggregation produces a ranked list.

5. **Per-candidate verification.** For each of the top 3 candidates by score, a targeted verification activity runs a fresh, candidate-specific search to check whether this exact use case is already implemented at the target company. If a candidate is `confirmed_existing`, it's filtered out and replaced with the next highest-scoring near-miss. This is the precise check that complements the broader existing-initiatives lookup in step 1.

6. **Select and enrich.** The verified top three plus a few near-misses are passed to an enrichment LLM call, which produces the polished customer-ready output: refined description, why-this-company explanation, example input and example output, implementation blueprint (which Mistral products + a mermaid architecture sketch), time-to-value and cost estimates anchored to precedents (or "unknown" if no precedent matches). The activity also returns a brief rejected appendix listing near-misses with one-line reasons they didn't make the cut.

7. **Meta-evaluate.** A reviewer LLM call examines the final report and asks: would a Mistral salesperson confidently bring this to a customer meeting? Does any proposal duplicate something the company already does? It returns a confidence score and, if low, identifies the weakest use case for targeted regeneration. At most one regeneration round per run.

The output is rendered as a composition of Rich UI Components in Le Chat (or in the standalone web app's main panel) — three `Card`s for the use cases, `Badge`s for impact and Mistral-suitability tiers, a `PieChart` for cost analysis, the rejected appendix as `Markdown` below, and the report metadata (quality signals) in a small footer.

### Temperature discipline by step

Each LLM call in the pipeline has an explicit, deliberate temperature, not a global default:

| Step | Temperature | Why |
|---|---|---|
| Research synthesis | 0.2 | Consistent, factual aggregation across sources |
| Generation (12 candidates) | 0.7 | Creative variety across candidates is the goal |
| Scoring (judge) | 0.2 (and 0.4 for self-consistency pass) | Consistent rubric judgments |
| Per-candidate verification | 0.1 | Deterministic interpretation of search results |
| Selection and enrichment | 0.4 | Some creativity in framing, mostly judgment |
| Meta-evaluation | 0.1 | Deterministic critic |

These are tuned per task, not picked from a default. Setting them explicitly is one of those details intermediate engineers know about but most don't bother to tune.

## What information we extract about a company

We extract only fields that have known downstream use. Fields the LLM does not know are flagged uncertain rather than fabricated.

The full schema is in `src/models.py`. It includes:

- **Identity** — name, legal name
- **Classification** — primary industry, sub-industries, geography, operating regions
- **Scale** — size tier, public/private status
- **Business** — business model, customer type (B2B/B2C/B2G/mixed), key products and services
- **Data and tech context** — likely data assets, known tech maturity (drives feasibility scoring)
- **Strategic context** — stated priorities, recent strategic moves (drives relevance and impact scoring)
- **Existing AI initiatives** — list of GenAI / ML deployments and announcements already made by the company; drives the iconic hard-gate, the per-candidate verification, and the meta-evaluator's duplicate check
- **Constraints** — regulatory context, data sovereignty concerns (drives feasibility and Mistral suitability)
- **Meta** — research confidence, contributing sources, verified-company flag

We deliberately do not extract financial details (exact revenue, employee count, stock price, founding year) because they don't drive any downstream decision in our pipeline. Asking for them costs tokens and invites fabrication.

### Worked example: Apple Inc.

To make the schema concrete, here is what `CompanyContext` looks like when populated for Apple:

```yaml
identity:
  name: Apple Inc.
  legal_name: Apple Inc.

classification:
  industry: Consumer Electronics
  sub_industries:
    - Hardware (smartphones, computers, wearables)
    - Software platforms (iOS, macOS, watchOS)
    - Digital services (App Store, Apple Music, iCloud, Apple TV+)
    - Semiconductor design (Apple Silicon)
  geography: United States, headquartered Cupertino California
  operating_regions:
    - North America
    - Europe (significant)
    - Greater China
    - Japan
    - Rest of Asia Pacific

scale:
  size_tier: enterprise
  public_or_private: public

business:
  business_model: |
    Vertically integrated consumer electronics company. Designs and sells
    premium hardware, monetizes installed base through high-margin services,
    controls the platform stack from silicon to OS to retail.
  primary_customers: mixed (heavy B2C, growing enterprise)
  key_products_or_services:
    - iPhone (largest revenue driver)
    - Mac and iPad
    - Apple Watch and AirPods
    - Apple Silicon (M-series, A-series)
    - Services (App Store, iCloud, Apple Music, Apple TV+, AppleCare)

data_and_tech:
  likely_data_assets:
    - Aggregated app usage telemetry across 1.5B+ active devices
    - Health and fitness sensor data (Apple Watch)
    - Privacy-preserving on-device ML traces
    - Retail and AppleCare service interactions
    - Developer ecosystem activity (App Store)
  known_tech_maturity: high

strategic_context:
  stated_priorities:
    - On-device AI / Apple Intelligence rollout
    - Vertical integration of silicon (Apple Silicon)
    - Privacy as competitive differentiator
    - Services revenue growth
    - Health and wellness platform expansion
  recent_strategic_moves:
    - Apple Intelligence launch (iOS 18, Apple Silicon Macs)
    - Vision Pro launch and platform investment
    - Ongoing China supply chain diversification

existing_ai_initiatives:
  - description: Apple Intelligence — on-device foundation models (~3B parameters)
    source: official_announcement
    confidence: high
  - description: Private Cloud Compute — privacy-preserving cloud LLM architecture
    source: official_announcement
    confidence: high
  - description: Siri 2.0 with OpenAI integration
    source: news
    confidence: high
  - description: AppleCare service triage assistance (rumored)
    source: industry_reporting
    confidence: medium

constraints:
  regulatory_context:
    - EU Digital Markets Act compliance
    - GDPR and global privacy regulations
    - Antitrust scrutiny in US, EU, Japan, Korea
  data_sovereignty_concerns: true

meta:
  research_confidence: 0.95
  research_sources:
    - wikipedia
    - recent_news
    - public_filings
    - job_postings
  is_verified: true
```

Every field in this schema has a downstream consumer in the generation, scoring, or enrichment steps. Fields with no clear downstream use (exact revenue, employee count, founding year, executive names) are deliberately not extracted because they cost tokens and invite fabrication.

## Cold-start handling and verification

The system is designed to work for any company, not just a curated allowlist. The verified-companies index is a large (~100k-500k entries) preprocessed lookup derived from Wikidata's company entities, used purely as a confidence-boost fast path for known entities. It is never used as a gate that blocks unknown companies.

The flow:

1. **Primary research path runs for every input.** All sub-tasks (Wikipedia, news search, jobs, existing initiatives) execute regardless of whether the company is in the verified index.
2. **Synthesis LLM call produces `research_confidence`.** This score reflects how coherently the parallel signals converge into a meaningful picture.
3. **Verified-index match boosts confidence deterministically.** If the company name fuzzy-matches an entry in the Wikidata-derived index (using rapidfuzz for token-based matching), `is_verified=True` and the research confidence floor is raised.
4. **Refusal triggers only on the unsalvageable case.** If `research_confidence < 0.5` AND `is_verified == False` AND no sub-task produced any usable signal, the system asks the user for context:

> "I couldn't find enough information about [company] to confidently generate use cases. Could you provide more context — industry, business model, geography — or try a different company name?"

In practice, a small or obscure company that isn't in the verified index will usually still have *some* signal — a website, a press release, a job posting — that lets the synthesis call produce a viable context. The verified index is a fast path for famous entities, not a filter on access.

## Edge cases

**Mistral AI as input.** Treated as a normal company. The system generates sincere internal use cases for Mistral itself, with a small playful acknowledgment in the cover summary that the user picked Mistral itself. No refusal, no special routing. Mistral's own existing AI capabilities are flagged in the existing-initiatives context, so the system doesn't recommend "Mistral should build an LLM."

**Other AI companies (Anthropic, OpenAI, Cohere, etc.) as input.** Treated normally. The Mistral-suitability dimension produces interesting comparative scoring — for example, a use case may score "neutral on Mistral suitability — equally feasible with any frontier provider" — and that nuance is preserved in the output.

**Competitors of the user's intended customer.** Handled the same way as any other company. The system does not refuse based on company identity.

**Unknown / fake companies.** Caught by the cold-start gate above.

## Quality signals on every output

Every report includes a small metadata footer with measurable quality signals. The signals are deliberately chosen to be the system's own honest assessment of its output, which is what a customer or sales engineer needs to evaluate the report.

- **Diversity** — average pairwise cosine distance between the three selected use cases. High diversity means the picks span different parts of the company's surface area, not three variations on the same theme. Used both for output quality reporting and as a quality gate during generation: if the 12 candidates are too similar to each other (cheap cosine check on candidate description embeddings), the generator is re-run with an explicit "diversify your candidates more — the previous attempt was repetitive" instruction.
- **Specificity** — fraction of references in each use case that point to specific entities from the company context (named products, named data assets, named priorities) versus generic terms. Implementation: tokenize the company context for entities, count overlaps with each candidate. Higher = more grounded. Renders as a small specificity bar per use case.
- **Mistral product diversity** — number of distinct Mistral products referenced across the three use cases. If all three say "Mistral API + Le Chat," product breadth is low. If they cover Le Chat, Document AI, Codestral, Mistral Compute — much broader. Renders as a small breakdown chart. Higher is generally better, indicating broader commercial coverage.
- **Time-to-value spread** — across the three use cases, surfaces the range of estimated time-to-value (e.g. "2-6 weeks for use case 1, 8-16 weeks for use case 2, 6-12 months for use case 3 — full ROI horizon spans 6 weeks to 12 months"). Customers care a lot about this; surfacing the range communicates that the report covers both quick wins and longer plays.
- **Cost-tier spread** — across the three use cases, indicates the mix of low/medium/high operating-cost tiers. A balanced mix is typically better than three high-cost or three low-cost picks.
- **Source coverage** — for each use case, which research sources (Wikipedia, news, jobs, precedents, existing initiatives) contributed evidence. Use cases grounded in multiple sources are more robust. Renders as small icons next to each use case.
- **Risks** — each use case includes the largest implementation risk surfaced by the meta-evaluator: data privacy, regulatory, technical maturity, integration complexity, etc. Honest risk-naming is a senior consultant move.
- **Fact-check pass rate** — a separate verification call checks each substantive claim about the company against the research context, including a check that the proposal is not already implemented. Returns pass/fail per claim. The aggregate rate is surfaced.

These are not just decoration — they are honest, measurable signals about what the system produced, intended to help a downstream user evaluate trustworthiness at a glance.

## Provenance — how we track the link from research to recommendation

Every use case carries two provenance fields:

- **`inspired_by`** — list of precedent IDs from the corpus that influenced this generation
- **`grounded_in`** — list of specific company-context fields the use case references (e.g. "Veolia.likely_data_assets[2]: smart meter telemetry")

These render in the output as small evidence footnotes under each use case. They make the output auditable and dramatically more credible to a customer who wants to know "why this, why us." This is the per-use-case "evidence" that complements the report-level quality signals — every individual recommendation can be traced back to its supporting research.

## Evaluation harness

The repository includes 5-10 hand-graded gold examples covering different industries (`tests/eval/gold_examples.jsonl`). When a prompt or activity is changed, an LLM-as-judge runs the eval set against both the old and new versions and reports which examples improved or regressed. This follows Anthropic's small-sample evaluation principle: 5-10 examples is enough to spot regressions and lets you iterate prompts with confidence rather than vibes.

## Prior work

Two published systems directly inform this architecture:

**Koshkin et al. (2025), MaRGen: Multi-Agent LLM Approach for Self-Directed Market Research and Analysis.** MaRGen introduces the pattern of generating multiple drafts of a structured business deliverable, evaluating them with a Judge agent against rubric criteria, and iteratively refining the result through a Reviewer-Writer feedback loop. We apply the Judge pattern in the scoring step (step 4) and the Reviewer-Writer pattern in the meta-evaluation step (step 7, with optional targeted regeneration). MaRGen also pioneers in-context learning from real consultant deliverables — we apply the same idea by injecting real shipped GenAI deployments from peer companies into the generation prompt.

**Anthropic Engineering (2025), How we built our multi-agent research system.** This work establishes the orchestrator-worker pattern with parallel subagents for breadth-first information gathering, the principle of scaling effort to query complexity, and the validation that single-call LLM-as-judge with a 0.0-1.0 score plus pass/fail is the most reliable evaluation pattern. We apply the orchestrator-worker pattern in the research step (step 1, with parallel sub-tasks for Wikipedia, news, jobs, precedents, and existing initiatives) and the LLM-as-judge pattern in scoring. We also follow the small-sample evaluation principle in our gold-example test set.

The combination of MaRGen for evaluation-and-refinement and Anthropic's pattern for parallel-research is intentional. The two systems address different stages of the pipeline and compose cleanly.
