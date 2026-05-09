# Data sources — preset vs live web

Every place the pipeline reads data, classified by whether it's a closed
curated file in this repo (PRESET) or an open-internet fetch (LIVE WEB).

## Summary

| Category | Sources |
|---|---|
| **PRESET (closed corpus)** | precedent corpus, verified-companies index, hand-curated few-shot examples, web-verify allowlist, SQLite cache |
| **LIVE WEB** | Wikipedia, Wikidata, Tavily news + targeted searches, direct HTTP fetches of news/career pages, generation `web_search` tool, per-candidate verification, web-verify rescue |

## Per-step breakdown

| # | Step | Source | Type | What it does |
|---|---|---|---|---|
| 1 | Research → Wikipedia | `en.wikipedia.org/api/rest_v1/page/summary/{name}` | **LIVE WEB** | Wikipedia article summary + Wikidata Q-id |
| 2 | Research → Wikidata | `wikidata.org/wiki/Special:EntityData/{Q-id}.json` | **LIVE WEB** | P17 (country) and other structured fields |
| 3 | Research → News | Tavily `/search` topic=news | **LIVE WEB** | ~5 recent press articles |
| 4 | Research → News deep-read | `httpx` GET on each news URL + `selectolax` extract | **LIVE WEB** | Full article body |
| 5 | Research → Jobs | `httpx` GET career pages + Playwright/Lightpanda fallback | **LIVE WEB** | Inferred AI/ML hiring direction (depth=high only) |
| 6 | Research → Existing initiatives | Tavily `/search` (specific queries) | **LIVE WEB** | Catches "X already deployed AI for Y" |
| 7 | Research → Verified-companies index | `data/companies_raw.jsonl` | **PRESET** | Hand-curated list; rapidfuzz match → confidence boost only |
| 8 | Research → Industry label polish | LLM read of already-fetched Wikipedia summary | preset *(no extra fetch)* | Cleans Wikidata's bureaucratic industry codes |
| 9 | Gap-fill | Tavily `/search` per missing field | **LIVE WEB** | Targeted fills for sparse research |
| 10 | **Retrieve precedents** | `data/precedents_raw.jsonl` (~2,150 entries) | **PRESET (NARROW)** | Every `inspired_by` reference must live here |
| 11 | Generate → context | `src/criteria.py` + `prompts.py:FEW_SHOT_EXAMPLES` | **PRESET** | 3 hand-curated example outputs |
| 12 | Generate → web_search tool | Tavily `/search` (≤4 calls per generation) | **LIVE WEB** | Generator can request live grounding mid-flight |
| 13 | Per-candidate verification | Tavily `/search` per top-K candidate | **LIVE WEB** | Duplicate detection + supporting-snippet extraction |
| 14 | Web-verify rescue | Tavily `/search` per unsupported claim (cap 12) | **LIVE WEB** | Last-chance lookup |
| 15 | Web-verify credibility | `src/web_verify.py:_ALLOWLIST_DOMAINS` (~40 domains + gov TLDs) | **PRESET** | Decides "verified" vs "corroborated" tier |
| 16 | Source-judge (v7) | Re-uses already-fetched ledger entries | preset *(no fetch)* | LLM judges (claim, snippet) coherence |
| 17 | Cache | `data/cache/genai_usecases.db` (SQLite) | **PRESET** *(populated by past LIVE WEB calls)* | Wikipedia / news / jobs cached |

## The two narrow places to know about

### 1. Precedent corpus (`data/precedents_raw.jsonl`, ~2,150 entries)

Every "comparable to X's deployment" claim with a corpus ID has to anchor
here. If a relevant precedent isn't in this file, the model picks a
worse-fit one or skips the citation. The corpus is built from:
- Google Cloud customer stories (`scripts/build_gcloud.py`)
- Evidently AI blueprints (`scripts/build_evidently.py`)
- Google Cloud blueprints (`scripts/build_blueprints.py`)

Real dataset, not synthetic, but narrower than the whole web's worth of
GenAI deployments. Free-text "comparable to Sephora's rollout" without a
corpus ID is allowed — bounded by the v6 quantitative-attribution rule.

### 2. Verified-companies index (`data/companies_raw.jsonl`)

Hand-curated list of company names with metadata. Fuzzy-matched
(`rapidfuzz`) against the input company name. Acts as a confidence
booster ("yes this is a real company we know about") — never a gate.
Absence just means we don't get the boost.

## Why this matters

The fact-check rescue chain (web-verify + source-judge) is **fully live
web**, gated by the curated domain allowlist for tier-1 trust. So a
claim flagged unsupported by meta-eval can still be rescued by reading
any source on the open internet — not limited to the preset corpus.

The two narrow sources above are the only places where the system reads
from a closed dataset. Everywhere else, the system can in principle
discover information published anywhere on the public web.
