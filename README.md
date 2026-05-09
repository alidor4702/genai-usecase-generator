# GenAI Use Case Generator for the Mistral Proto Team

Take-home submission for the Applied AI Engineer role on the Mistral Proto Team.

## What this is

A workflow that takes a company name and returns three GenAI use cases that are, for that specific company, relevant, iconic, and high-impact — grounded in real shipped deployments at peer companies, scored against an explicit five-dimension rubric, and explicitly checked against what the company is *already* doing across multiple verification layers.

The system is built on Mistral Workflows and is published publicly as a Le Chat assistant ("GenAI Use Case Generator"). A standalone web app at [public URL] provides the same functionality with a custom UI.

## Try it

**In Le Chat.** The assistant is published publicly to Le Chat's assistants directory as **"GenAI Use Case Generator"**. Search for it by name in your Le Chat assistants tab, install it with one click, type a company name, optionally choose a focus area, and wait roughly 30-90 seconds for the report. No invite link needed.

**Standalone web app.** Visit [public URL]. Type a company name in the input field. Adjust focus area and criteria weights if desired. Click generate.

**Locally.** See "Running locally" below.

## What good output looks like

Sample reports for several companies are checked into `docs/examples/`:

- [`veolia.md`](docs/examples/veolia.md) — water utilities, France
- [`bnp_paribas.md`](docs/examples/bnp_paribas.md) — banking, France
- [`carrefour.md`](docs/examples/carrefour.md) — retail, France
- [`loreal.md`](docs/examples/loreal.md) — consumer beauty, France
- [`mistral_ai.md`](docs/examples/mistral_ai.md) — yes, the system generates use cases for Mistral itself, sincerely and with a small acknowledgment of the recursion

These are the same outputs the system produces live; they are committed so reviewers can evaluate output quality without running the code.

## How it works (in one paragraph)

A user submits a company name and optionally a focus area. The workflow runs in seven stages: it researches the company across multiple parallel sources (Wikipedia, recent news, job postings, peer precedents, and the company's existing AI initiatives); retrieves real shipped GenAI deployments from companies in the same industry; generates 12 candidate use cases grounded in those precedents while explicitly excluding things the company is already doing and biasing toward novel directions; scores each candidate on five criteria (relevance, iconic potential, impact, feasibility, Mistral suitability) using a judge LLM with self-consistency and a hard gate against duplicates of existing initiatives; runs a per-candidate targeted verification on the top 3 to catch any specific deployment the broader sweep missed; selects the verified top three and enriches each with example I/O, an architecture blueprint, and precedent-anchored estimates; finally a meta-evaluator reviews the report and triggers targeted regeneration of the weakest use case if confidence is low. The output renders as composed Rich UI Components — Cards, Badges, PieChart, mermaid blueprints — directly in Le Chat.

For the full architecture, see [`docs/architecture.md`](docs/architecture.md). For the methodology and criteria definitions, see [`docs/methodology.md`](docs/methodology.md).

## Methodology summary

Five criteria, scored 1-10 with rationale:

1. **Relevance** — touches a core business workflow at scale, with the data assets and stated priorities to make it work
2. **Iconic potential** — visibly associated with this company specifically, exploits something distinctive, AND not something the company is already doing (hard gate)
3. **Estimated impact** — measurable financial or strategic value large enough to justify GenAI's overhead, anchored to peer deployments
4. **Feasibility** — shippable with current tech in a customer engagement timeline, no fundamental research needed
5. **Mistral suitability** — leans into what Mistral does distinctively (sovereignty, open-weight, multilingual, cost, customer alignment)

Default weights: 20% each. Configurable per run via a `FormInput` with `NumberField` sliders.

A core design principle is the **proven-elsewhere vs already-done-here distinction**. A use case that has been deployed at peer companies but not yet by the target company is the strongest possible position — it is both feasible (precedent exists) and iconic (this company hasn't done it yet). Use cases the target company is already pursuing are filtered out across four independent verification layers: a broad existing-initiatives lookup in research, the scorer's iconic hard-gate, a per-candidate targeted verification on the top 3, and the meta-evaluator's final pass.

The system is also explicitly biased against derivative output. The generator is required to produce at least 3 of its 12 candidates as novel directions — extensions, combinations, or original framings — rather than direct adaptations of any single precedent.

The full criteria definitions, with positive and negative examples, are in [`docs/methodology.md`](docs/methodology.md) and codified as Pydantic objects in [`src/criteria.py`](src/criteria.py).

## Tech stack

| Component | Tech |
|---|---|
| Workflow orchestration | Mistral Workflows SDK with `mistralai` plugin (`InteractiveWorkflow`) |
| Generation, synthesis, meta-eval | Mistral Medium 3.5 (`mistral-medium-2604`) |
| Selection and enrichment | Mistral Large 3 (`mistral-large-2512`) |
| Scoring, per-candidate verification | Mistral Small 4 (`mistral-small-2603`) |
| Embeddings | Mistral Embed (`mistral-embed`) |
| Backend (standalone surface) | FastAPI + async Python 3.12 |
| Frontend (standalone surface) | Next.js 14 App Router + TypeScript + Tailwind |
| Validation | Pydantic v2 |
| Search/news/verification | Tavily Search API |
| Wikipedia | Wikipedia/Wikidata REST APIs |
| Direct fetches | httpx (async) |
| Fuzzy company name matching | rapidfuzz |
| HTML parsing | selectolax |
| JS-heavy scraping fallback | Playwright + Lightpanda CDP backend |
| Data layer | SQLite (companies index, precedents, cache) |
| Tooling | uv, ruff, pyright, pytest |
| Deployment | Render/Railway (backend) + Vercel (frontend) |
| Production migration path | Postgres + pgvector + Redis (mentioned, not built) |

## Engineering rigor

A few details that distinguish this submission from the basic "company name → 3 use cases" build:

- **Structured outputs at every step.** Every LLM call produces Pydantic-validated JSON; nothing is parsed from prose.
- **Temperature discipline by step.** Each LLM call has an explicit temperature tuned to its task — 0.2 for synthesis and scoring, 0.7 for generation, 0.4 for enrichment, 0.1 for meta-eval. Tuned, not defaulted.
- **Self-consistency on scoring.** The scorer runs twice at different temperatures and averages, reducing single-call noise on the most quality-sensitive step.
- **Few-shot anchoring.** The generator prompt includes 1-2 hand-curated example outputs from other companies, the highest-impact technique for output quality.
- **Negative examples in criteria.** Each criterion includes "what bad looks like" alongside "what good looks like" — the negative anchor gives the model a cleaner signal of what to avoid.
- **Output diversity guard.** After generation, a cosine similarity check on the 12 candidates triggers a regeneration if they're too similar. Catches the boring-output failure mode automatically.
- **Determinism in the workflow class.** No `datetime.now()`, no `random()`, no I/O in the orchestrator. All side effects in activities. Required for the Mistral Workflows replay model.
- **Activity timeouts.** Each activity has an explicit `start_to_close_timeout` so an unresponsive call can't hang the workflow.
- **Tiered TTL caching.** Stable facts cache for 30 days, news for 24h, etc. — same backend interface for SQLite (local) and Redis (deployed).
- **Per-use-case provenance.** Every generated use case carries `inspired_by` (precedent IDs) and `grounded_in` (company-context fields) — auditable and credible.
- **Eval harness with 5-10 hand-graded gold examples.** LLM-as-judge runs the eval on every prompt change to catch regressions.

## Running locally

### Prerequisites

- Python 3.12
- `uv` for dependency management (`pipx install uv`)
- A Mistral AI Studio account with an API key
- A Tavily API key (free tier sufficient)
- Node.js 20+ for the standalone web app (optional)

### Setup

```bash
git clone [repo-url]
cd genai-usecase-generator
uv sync

cp .env.example .env
# Edit .env and set MISTRAL_API_KEY and TAVILY_API_KEY
```

### Initialize the local data layer

```bash
uv run python scripts/build_data.py
```

This builds the local SQLite database in `data/genai_usecases.db` from the source files described in "Data sources" below.

## Data sources

The system relies on two pre-built local indexes, both constructed by `scripts/build_data.py`:

### Verified-companies index (~100k-500k entries)

- **Source:** Wikidata SPARQL endpoint, queried for entities of class `Q4830453` (business) and `Q783794` (company)
- **Fields kept:** name, aliases, primary industry, country, Wikidata ID
- **Use:** confidence-boost fast path during research (matched companies set `is_verified=True` and raise the confidence floor)
- **Update cadence:** rebuilt manually when desired; static for prototype

### Precedent corpus (~1,500-2,000 entries)

A curated collection of real shipped GenAI deployments at named companies, used as in-context examples during candidate generation. Built from three complementary sources:

- **[Evidently AI — 800+ ML and LLM use cases database](https://www.evidentlyai.com/blog/gen-ai-applications)** — a structured CSV (805 entries) with company, industry, short description, source title, technology, tags, year, and link. The CSV is checked into the repo as `data/raw/evidently_805.csv`. The build script uses every entry for shallow precedents and deep-reads the linked source for the ~88% of entries that are text media (engineering blogs, company blogs, technical articles). Non-text links (YouTube videos, podcasts) keep their CSV metadata only. This single source provides 700+ deep precedents at minimal effort because the CSV is already structured.

- **[Google Cloud — 1,001 real-world generative AI use cases](https://cloud.google.com/transform/101-real-world-generative-ai-use-cases-from-industry-leaders)** — concrete deployments at named companies organized by 11 industries × 6 agent types (Customer, Employee, Creative, Code, Data, Security). Each entry is 1-3 sentences. Fetched via plain HTTP and parsed; provides breadth across industries and explicit Google Cloud customer examples (Mercedes Benz, Mercari, Volkswagen, etc.).

- **[Google Cloud — 101 real-world gen AI use cases with technical blueprints](https://cloud.google.com/blog/products/ai-machine-learning/real-world-gen-ai-use-cases-with-technical-blueprints)** — the *technical complement* to the 1,001 list. Each of 101 entries includes business challenge + tech stack + step-by-step blueprint flow + an architecture diagram. Provides depth and architecture vocabulary for the implementation blueprints in the enrichment step.

All entries are normalized to a consistent schema (`{company, industry, title, description, outcome, deep_content, id, embedding}`) during the build. The `deep_content` field holds the fetched full article text where available, or null where the source was non-text or fetch failed. Vendor product names (Vertex AI, BigQuery, Bedrock, etc.) are stripped during preprocessing — only the use case content survives — so the generator does not accidentally recommend non-Mistral products to customers.

### Deep-reading at corpus build time

For the Evidently CSV, the build script implements a tiered fetch:

1. **All 805 entries** become precedents with the CSV's structured metadata (always available).
2. **Text-media URLs** (engineering blogs, company blogs, general web text — ~88% of the CSV) are fetched via `httpx`, parsed with `selectolax`, content-extracted, and stored in the `deep_content` field. This deepens roughly 700 of the 805 entries with the actual article body.
3. **Non-text URLs** (YouTube, podcasts — ~5%) keep CSV metadata only.
4. **Ambiguous URLs** (PDFs, social — ~7%) are attempted with a short timeout; on failure they keep CSV metadata only.

The build script logs which entries were deep-read vs metadata-only, so the corpus's depth profile is auditable.

### Methodology references (cited, not ingested as data)

- **[Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)** — informs the orchestrator-worker pattern in research and the LLM-as-judge pattern in scoring.
- **[Koshkin et al. (2025), MaRGen](https://arxiv.org/abs/2508.01370)** — informs the Judge agent pattern in scoring and the Reviewer-Writer feedback loop in meta-evaluation.

These are cited in `docs/methodology.md` and `docs/architecture.md`; they shape the system's design but are not loaded as runtime data.

### Reproducibility

`scripts/build_data.py` is fully reproducible: re-running it rebuilds the SQLite database from raw sources. To rebuild from scratch, delete `data/genai_usecases.db` and re-run.

The `data/raw/` subdirectory contains the input files: the Evidently CSV (committed) and any cached fetched HTML for offline rebuilds. The Google Cloud pages are fetched live during the build (they're stable, server-rendered HTML that fetches reliably with plain `httpx`).

### Run the workflow worker

```bash
uv run python -m src.workflow
```

This starts a Mistral Workflows worker that listens for workflow invocations.

### Run the standalone web app (optional)

In a second terminal:

```bash
cd standalone
npm install
npm run dev
```

Then open `http://localhost:3000`.

### Run an example query from the CLI

```bash
uv run python scripts/run_example.py "Veolia"
uv run python scripts/run_example.py "BNP Paribas" --focus operations
uv run python scripts/run_example.py "L'Oréal" --weights '{"iconic": 0.4}'
```

## Repository layout

```
.
├── README.md                       # this file
├── pyproject.toml                  # deps, ruff, pyright config
├── .env.example                    # required env vars
├── docs/
│   ├── methodology.md              # criteria definitions, philosophy
│   ├── architecture.md             # full pipeline, mermaid diagrams
│   └── examples/                   # sample outputs
├── src/
│   ├── workflow.py                 # InteractiveWorkflow class (deterministic, orchestration only)
│   ├── activities/                 # one file per pipeline step
│   │   ├── research.py             # parallel sub-tasks for the research step
│   │   ├── retrieve.py
│   │   ├── generate.py
│   │   ├── score.py
│   │   ├── verify_per_candidate.py # per-candidate targeted verification
│   │   ├── select_enrich.py
│   │   └── meta_evaluate.py
│   ├── research/                   # research sub-task implementations
│   │   ├── wikipedia.py
│   │   ├── news.py                 # Tavily integration
│   │   ├── jobs.py                 # httpx + Playwright/Lightpanda fallback
│   │   ├── existing_initiatives.py
│   │   └── verification.py         # verified-companies index lookup (rapidfuzz)
│   ├── ui/
│   │   └── render.py               # Rich UI Components composition (Card, Badge, PieChart, etc.)
│   ├── models.py                   # all Pydantic models
│   ├── criteria.py                 # criteria definitions and weights
│   ├── prompts.py                  # all LLM prompt templates
│   ├── precedents.py               # corpus loading and retrieval (cosine + optional MMR)
│   ├── db.py                       # SQLite layer (companies, precedents, cache)
│   ├── cache.py                    # cache abstraction (SQLite + Redis backends)
│   └── quality_signals.py          # diversity, specificity, etc.
├── data/
│   ├── genai_usecases.db           # SQLite database (created by build_data.py)
│   └── raw/                        # raw input sources for the build
│       └── evidently_805.csv       # Evidently AI's 805-entry use cases database
├── tests/
│   ├── test_models.py
│   ├── test_criteria.py
│   ├── test_quality_signals.py
│   └── eval/
│       ├── gold_examples.jsonl     # 5-10 hand-graded gold outputs
│       └── run_eval.py             # LLM-as-judge eval harness
├── standalone/                     # Next.js standalone web app
└── scripts/
    ├── build_data.py               # builds SQLite DB from raw sources
    └── run_example.py
```

## Cost characteristics

Per-run end-to-end cost on Mistral's current API pricing, with medium research depth and no cache hits, is roughly **$0.08-0.20**. Cache hits on stable facts (Wikipedia, verified-list, existing initiatives, prior verification searches) drop repeat queries on the same company to **$0.03-0.06**.

The system uses a deep-read pattern at runtime: rather than relying on search snippets alone, the research and per-candidate verification activities fetch full article bodies for the top 2-3 most relevant results and feed those into the synthesis LLM call. This adds roughly $0.02 per run vs snippet-only, traded against meaningfully better grounding.

This is consistent with Anthropic's published finding that multi-agent systems use roughly 15× the tokens of a single chat interaction. Our pipeline is bounded toward the lower end (7-9×) because of fixed fan-out at the research step, deep-reading, and a fixed pipeline depth.

For a Proto Team customer engagement, this cost is negligible relative to the scoping value produced.

## Quality and evaluation

Every run surfaces measurable quality signals in the output metadata footer:

- **Diversity** of the three selected use cases (cosine distance)
- **Specificity** of references to the company context
- **Mistral product diversity** across use cases
- **Time-to-value spread** across the three (range of weeks-to-pilot, weeks-to-production)
- **Cost-tier spread** across the three (mix of low/medium/high operating cost)
- **Source coverage** — which research signals grounded each use case
- **Risks** surfaced per use case
- **Fact-check pass rate** on substantive claims, including the non-duplication check

The repository also includes a small evaluation harness (`tests/eval/`) with 5-10 hand-graded gold examples covering multiple industries. An LLM-as-judge graded against these examples is run on every prompt change to catch regressions. This follows Anthropic's small-sample evaluation principle.

## Known limitations

- **AI-vendor-as-target produces tautological proposals.** When the target company is itself an AI/LLM provider — Mistral AI, OpenAI, Anthropic — the system frames use cases as "X could deploy Y for itself" which is structurally tautological. The pipeline was designed for companies *adopting* AI, not building it. v6 Mistral AI run lands at specificity 0.50 across all three because the proposals describe Mistral's existing products (Forge, fine-tuning SDK, multilingual devrel). For a v7 fix this could be a prompt branch ("if target is itself an AI/LLM company, frame as internal applications"), but the cleaner answer is "this isn't the system's target market" — most customers in the Mistral Proto Team's pipeline are not AI vendors.
- **Precedent corpus is closed (~2150 deployments).** Every `inspired_by` reference must live in `data/precedents_raw.jsonl`. If a relevant peer deployment isn't in the corpus, the model anchors on a weaker match or skips the citation. Free-text "comparable to Sephora's rollout" is allowed (with the v6 quantitative-attribution rule) so this doesn't block grounding, but it does mean retrieval has narrower recall than a full-web search would.
- **Tier-2 corroborated rescues use regex anchor matching.** v6 web-verify promotes a non-allowlist source if its body contains a number or capitalised entity from the claim. v7 source-judge is the corrective layer (LLM reads the snippet and decides), but if the LLM judge call fails (transient API error), the system fails open and keeps the corroboration.
- **`current_step` in the FastAPI standalone-app surface is set once at run start.** `state.current_step` is initialised when the run kicks off and never updated during pipeline execution — the live UI derives the current step from the latest trace event instead. The Mistral Workflows class (`src/workflow.py`) updates `self.current_step` correctly per phase; it's only the `src/api.py` direct-pipeline path that holds it constant. Acceptable because the trace stream is the source of truth for the UI.

## What I'd add with more time

- **Score self-consistency ablation.** Step 4 currently runs the scoring pass twice — once at T=0.2, once at T=0.4 — and merges. This is a known-good technique for noisy LLM scoring, but its marginal value at this specific step has never been measured on this corpus. Cost is ~18s per run. Post-submission ablation: run all 5 example companies single-pass at T=0.3, compare top-3 selections vs. the two-pass version. If they match >80% of the time, we're paying ~18s × every run for noise. If they diverge, the second pass earns its keep.
- **Parallelize enrichment per use case.** The single Mistral Large call writes all 3 use cases together so the model has a global view (cross-cutting concern, Mistral product spread, top-risk uniqueness). Splitting into 3 parallel calls would save ~30s but risks losing those global guarantees. Worth measuring with a 5-company A/B before deciding.
- **Real-time event-driven cache invalidation.** Hook into news APIs to force-refresh research when a company has a major announcement, rather than relying purely on TTLs.
- **Earnings call and 10-K ingestion.** For public companies, parse recent earnings transcripts and SEC filings to enrich the strategic priorities and existing-initiatives signals.
- **Multi-language support.** Generate outputs in the language of the company's primary geography (French for Veolia, German for SAP, Spanish for Banco Santander).
- **Canvas Editing for human-in-the-loop refinement.** The Mistral Workflows SDK ships a `CanvasInput` primitive that lets users edit a returned canvas inline in Le Chat. We could add a second `wait_for_input` after the first generation, presenting the report as an editable canvas, letting the user push back on any of the three use cases and trigger targeted regeneration of just that one.
- **Streaming generation visible in Le Chat.** Use `RemoteSession(stream=True)` with a Mistral Workflows agent for the candidate generation step so the user sees candidates appearing in real-time. UX polish, low effort.
- **MMR diversification on retrieved precedents enabled by default.** Currently MMR is optional, only triggered when top-k similarity scores are clustered. Could be made the default with a tunable trade-off parameter.
- **Production data layer.** Migrate from SQLite to Postgres + pgvector for embeddings; cache moves to Redis. Same code paths, different connection strings.
- **Production deployment.** Containerize backend and frontend, deploy as Kubernetes Deployments + Services with the Mistral API key as a Secret, add an HPA on CPU, set up GitHub Actions for ruff + pyright + tests on PR + automated deploy on main, and observability via structured logging + per-request traces.
- **Differential evaluation.** When iterating on prompts, run the eval set on the old and new prompt simultaneously and surface which examples improved or regressed, not just the aggregate score.
- **Active corpus expansion.** When a generated use case is judged high-quality and customer-ready, optionally write it back to the precedent corpus, so the system learns from its own best outputs.
- **Lightpanda for high-volume scraping.** The current build uses Lightpanda only as a fallback for JS-heavy career pages. At higher concurrency (mass research across thousands of companies), Lightpanda becomes the primary headless browser layer for its 11× speed and 9× memory advantages over Chrome.

## Post-build deliverables (planned for after the system ships)

After the system is built and validated, the following audience-facing artifacts will be produced as supplemental deliverables:

- **Notion page or PDF write-up** — architecture and methodology in a polished, readable format. Designed to be skimmable by both technical and non-technical readers, with a layered structure (executive summary at top, deep technical sections lower).
- **Visual deck (PowerPoint or Keynote)** — a 10-15 slide presentation aimed at non-technical or semi-technical audiences. Focused on the *why* and the *output* rather than the implementation details. Suitable for use in customer conversations or internal stakeholder reviews.
- **Optional: video walkthrough** — a short demo recording showing a workflow run end-to-end on Le Chat, with voice-over explanation.

These are noted as planned post-build deliverables rather than scoped into the take-home itself, which focuses on the working system + the technical documentation in `docs/`.

## Prior work

This system explicitly borrows from two published works:

- **Koshkin et al. (2025), MaRGen: Multi-Agent LLM Approach for Self-Directed Market Research and Analysis.** The Judge agent pattern in scoring, the Reviewer-Writer loop in meta-evaluation, and the in-context learning from real professional examples pattern in generation are all direct applications.
- **Anthropic Engineering (2025), How we built our multi-agent research system.** The orchestrator-worker pattern with parallel subagents in research, the depth-scaling principle, and the LLM-as-judge rubric pattern are all direct applications.

See `docs/methodology.md` and `docs/architecture.md` for specific mappings from prior work to specific pipeline stages.

## Submission notes

Mistral AI Studio organization ID: [your org ID]

Contact during review:

- Take-home questions: `paul.devillers@mistral.ai`, `maxime.langelier@mistral.ai`
- Repository owner: [your name and contact]

Built for the Mistral Proto Team Applied AI Engineer take-home, May 2026.
