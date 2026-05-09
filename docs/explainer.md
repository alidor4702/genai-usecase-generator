# Plain-English pipeline explainer

How the GenAI Use Case Generator works, written for someone who hasn't
seen the codebase. No prompts, no model names, no jargon. The two
visualised versions of this content live at `/how-it-works`
(non-technical) and `/architecture` (technical) when the standalone web
app is running.

## What goes in, what comes out

**Input:** a company name. Optional knobs: which scoring criteria
matter most (weights), what the use cases should focus on (general /
operations / customer / sustainability), how deep the research should
go (low / medium / high).

**Output:** three customer-ready GenAI use cases, each with:
- a clear title and description
- why it fits THIS company specifically (not "any retailer")
- a literal example of what an end user would type and what the system
  would return
- an architecture sketch (mermaid diagram)
- a time-to-value estimate (precedent-anchored OR honestly tagged as a
  ballpark)
- a cost / complexity / impact rating
- the top implementation risk
- which Mistral products are involved
- citations for every concrete claim
- a per-claim transparency block showing which claims passed
  verification and which were rescued (with chips like
  `[verified ↗]` or `[judge: rejected]`)

## The 13-step chain

The pipeline runs as a chain of typed activities. Each activity reads
from the previous step's output and writes the next step's input. No
shared global state.

### Step 1 — Research

Reads four sources at once: Wikipedia, recent news (with full article
bodies), the company's career pages (for AI/ML hiring direction), and
public mentions of AI projects they already run.

Hands all four signals to the synthesizer model, which writes one
structured `CompanyContext` JSON: industry, business model, data
assets, stated priorities, regulatory context, and the
`existing_ai_initiatives` list (so we can avoid proposing duplicates).

A `free_text_notes` field captures rich detail that doesn't fit the
structured fields — named partnerships, recent announcements, regional
specifics. This is treated as PRIMARY grounding by every downstream
step.

Confidence score on top. If the signal is too sparse (niche company,
stub Wikipedia, no news), the system **refuses gracefully** instead of
fabricating.

### Step 1b — Gap-fill

Whatever fields the synthesizer left empty get one targeted Tavily query
each. Results re-feed into a second synthesis pass. Cheap, catches
common gaps.

### Step 2 — Retrieve precedents

The company context is embedded (vector representation). The system
runs cosine-similarity search against ~2,150 real GenAI deployments at
other companies (Google Cloud customer stories + Evidently AI
blueprints). Returns the top 8 industry-similar examples.

These are evidence of feasibility, not templates. The generator MUST
mark at least 3 of its 12 candidates as novel — not a direct adaptation
of any single precedent.

### Step 3 — Generate 12 candidates

A generation model writes 12 candidate use cases. Each candidate cites:
- which company-specific facts it's grounded in (`grounded_in`: paths
  into the CompanyContext)
- which peer precedents it draws from (`inspired_by`: corpus IDs)
- which live-web evidence it pulled mid-draft (the model has a
  `web_search` tool with a 4-call budget)

The generator self-marks any near-duplicate siblings (e.g. two variants
of "sustainability supplier audit") with `near_dup_of: <other_id>` so
the next stage can drop one.

### Step 4 — Score against five criteria

Every candidate gets five scores from a calibrated rubric:
1. **Relevance** — touches a core workflow at scale
2. **Iconic potential** — visibly distinctive for THIS company AND not
   already done by them (hard-capped at 1-2 if it overlaps with
   `existing_ai_initiatives`)
3. **Estimated impact** — measurable financial or strategic value
4. **Feasibility** — shippable in a customer engagement timeline
5. **Mistral suitability** — leans into Mistral's distinctive strengths

To stay calibrated, scoring runs twice with slightly different
randomness and merges results.

### Step 5 — Per-candidate verification

The top-scoring candidates each get a targeted live web search asking
"has this company already deployed this?". Possible verdicts:
- `confirmed_existing` → drop the candidate, replace with the next best
- `partial_overlap` → keep with a "builds on existing" note
- `pass` → proceed (default for inconclusive evidence)

Same step also extracts up to 5 **supporting snippets** per candidate
— claim-relevant lines pulled live from the web search results — that
flow into the next step as primary grounding.

### Step 6 — Select + enrich

The top 3 verified candidates go to the premium model, which writes the
customer-ready prose: refined description, why_this_company, example
input/output, blueprint pattern, mermaid architecture sketch, time-to-
value, cost tier, top implementation risk, Mistral products.

If the top-3 contains a `near_dup_of` pair (two siblings the generator
flagged as overlapping), the lower-scored one gets swapped out for the
next non-linked candidate from the appendix.

### Step 6a — Polish

Reads the enriched prose against the FULL evidence pool (not just the
explicitly cited entries). For any specific number in the prose:
- If verifiable from any pool entry → keep the number, attach inline
  citation, add the source to the use case's `evidence_ids`.
- If not verifiable from the pool → leave the number alone (v7 change).
  The verification chain that follows will decide.

### Step 7 — Meta-evaluate

A senior-reviewer pass examines the 3 use cases, the cited precedents'
deep content, and the FULL evidence pool. Outputs:
- `sales_engineer_ready` (yes/no)
- `confidence` (0-1, anchored to the supported-fraction of claims)
- the weakest use case (if confidence is low and we have budget, the
  weakest gets regenerated and re-evaluated)
- the biggest cross-cutting concern across all three
- a `claims` list — every substantive factual claim across the three
  use cases, each marked supported (with the supporting source) or
  unsupported

The claim-extraction rules cover specific named entities, specific
numbers, specific data-asset claims ("X has historical sales data"),
and qualitative peer-deployment claims ("comparable retailers report
material reductions"). Each gets verified independently — sentences
mixing entities and numbers are split into atomic claims.

### Step 7c — Web-verify rescue

For every claim meta-eval flagged unsupported, run one targeted live
web search. Apply a deterministic two-tier credibility classifier:
- **Tier 1 (`verified`):** domain is on a curated allowlist (Reuters,
  FT, Bloomberg, Le Monde, WSJ, Forbes, HBR, government / EU regulator,
  company-official). Auto-promote to supported.
- **Tier 2 (`corroborated`):** non-allowlist domain whose body contains
  a number or capitalised entity from the claim. Promote with a
  distinguishing chip in the report.

Capped at 12 rescue searches per run. The promoted source gets appended
to the evidence ledger.

### Step 7d — Source-judge (v7)

For every claim still marked supported with a resolvable URL (rescue or
ledger-cited), a small model reads the (claim, source-snippet) pair and
decides: does this snippet actually support the claim, or just contain
related entities in unrelated context?

Strict default: inconclusive evidence flips back to unsupported with a
`judge_rejected` chip in the report. This catches the failure mode
where a URL got promoted because it merely mentioned the company name
and a number, without actually backing the specific claim.

### Step 7e — Final qualify (v7)

For each use case, gather the still-unsupported numeric/named claims
(after web-verify and judge). A small model rewrites the prose so those
specific assertions become qualitative ("a meaningful reduction" /
"a multi-petabyte data platform"), leaving everything else verbatim.

This replaces the v6 pre-strip-at-polish behavior with a post-
verification surgical rewrite. Numbers get every chance to be anchored
before they're killed.

### Quality signals

The final report carries:
- LLM-graded **diversity** across the 3 use cases (different surfaces /
  data assets / blueprint patterns?)
- LLM-graded **specificity** per use case (how grounded in this
  company's actual facts vs. how generic?)
- **fact-check pass rate** (post-verification supported-fraction)
- **Mistral product diversity** (how varied are the suggested products?)
- **TTV / cost spread** across the three

### Render

Two parallel render paths:
- **Markdown** (sent to the standalone web app) — full report with
  per-claim transparency block.
- **Components mode** (Card / Badge / PieChart) — for the Mistral
  Workflows / Le Chat surface, which renders these primitives natively.

## Where it can fail

| Failure mode | What happens | Caught by |
|---|---|---|
| Niche company, sparse signal | Refusal | Confidence gate after Step 1b |
| Real claim flagged unsupported by meta-eval | Rescued via web-verify | Steps 7c, 7d |
| URL got promoted but doesn't actually support the claim | Flipped back to unsupported | Step 7d source-judge |
| Number can't be anchored anywhere | Surgically rewritten qualitative | Step 7e |
| Fabricated peer-deployment % | Blocked at generation/enrichment by the no-quantitative-peer rule | Prompt rule |
| Two near-duplicate use cases in top-3 | One swapped out for next non-linked appendix candidate | Step 6 selection |
| Target IS an AI vendor (Mistral, OpenAI, Anthropic) | Tautological proposals, lower specificity scores | **Known limitation** — see README |

## Live observability

When the standalone web app is running, every step emits two SSE events
— `step_start` (the moment the activity begins) and `step_complete`
(when it finishes, with duration and a one-line outputs summary). The
frontend renders a "running" shimmer while waiting for `step_complete`,
flips to a finished card via id-merge.

Run id is shown at the top of the run view. The whole trace is also
returned via `/status/{run_id}` for inspection.
