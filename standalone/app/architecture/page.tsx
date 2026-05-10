"use client";
import AnimatedBackground from "../components/AnimatedBackground";
import MermaidDiagram from "../components/MermaidDiagram";
import SiteNav from "../components/SiteNav";

/**
 * /architecture — technical visual explainer.
 *
 * Audience: an engineer reviewing the implementation. Includes the full
 * pipeline mermaid diagram, the data-source map (preset vs live web),
 * the model + temperature table, the evidence-ledger flow, and the v7
 * verification chain. No marketing copy.
 */

// Pipeline rendered LR (left-to-right) so it uses the page width
// instead of a tall vertical column. Each phase wraps to multiple
// lines via <br/> so the boxes stay narrow but readable.
const PIPELINE_MERMAID = `
flowchart LR
  Input([Company name + knobs])
  Research["1. Research<br/>Mistral Medium 3.5"]
  GapFill["1b. Gap-fill<br/>Tavily"]
  Retrieve["2. Retrieve<br/>cosine top-k"]
  Generate["3. Generate 12<br/>Mistral Medium 3.5<br/>+ web_search"]
  Score["4. Score 5 criteria<br/>Mistral Small × 2"]
  Verify["5. Verify top-3<br/>Tavily + Small"]
  Enrich["6. Enrich top-3<br/>Mistral Large 3"]
  Polish["6a. Polish<br/>full-pool"]
  MetaEval["7. Meta-eval<br/>per-claim<br/>Mistral Medium 3.5"]
  WebVerify["7c. Web-verify<br/>2-tier rescue"]
  Judge["7d. Judge<br/>self-correcting<br/>Mistral Small"]
  FinalQ["7e. Final qualify<br/>Mistral Small"]
  Signals["Quality signals"]
  Output([Report + persistence])

  Input --> Research --> GapFill --> Retrieve --> Generate --> Score --> Verify --> Enrich --> Polish --> MetaEval --> WebVerify --> Judge --> FinalQ --> Signals --> Output

  classDef llm fill:#fa552e,stroke:#fdba8c,color:#fff,stroke-width:2px
  classDef live fill:#1e3a8a,stroke:#60a5fa,color:#dbeafe,stroke-width:2px
  classDef preset fill:#064e3b,stroke:#34d399,color:#d1fae5,stroke-width:2px
  classDef io fill:#1f2530,stroke:#fa552e,color:#fff,stroke-width:1.5px
  class Research,Generate,Score,Verify,Enrich,Polish,MetaEval,Judge,FinalQ,Signals llm
  class GapFill,WebVerify live
  class Retrieve preset
  class Input,Output io
`;

const LEDGER_MERMAID = `
flowchart LR
  W[Wikipedia summary] --> L((Evidence Ledger))
  N[News deep-read] --> L
  J[Career pages] --> L
  E[Existing initiatives] --> L
  G[Generation web_search results] --> L
  V[Per-candidate verification deep-reads] --> L
  S[Verifier supporting_snippets] --> L
  R[Web-verify rescue Tavily hits] --> L
  L --> Polish[Polish reads pool excerpts]
  L --> MetaEval[Meta-eval verifies claims]
  L --> Judge[Judge re-reads cited sources]

  classDef src fill:#1e3a8a,stroke:#60a5fa,color:#dbeafe,stroke-width:1.5px
  classDef ledger fill:#fa552e,stroke:#fdba8c,color:#fff,stroke-width:2px
  classDef sink fill:#7c2d12,stroke:#fdba74,color:#fed7aa,stroke-width:1.5px
  class W,N,J,E,G,V,S,R src
  class L ledger
  class Polish,MetaEval,Judge sink
`;

const VERIFICATION_CHAIN_MERMAID = `
flowchart TD
  C[Substantive claim] --> Pool{Anchored in<br/>evidence pool?}
  Pool -- yes --> Pass1[supported<br/>source_kind=evidence:ev-id]
  Pool -- no --> WV[7c. Web-verify Tavily]
  WV --> Tier{Domain credibility}
  Tier -- "allowlisted (Reuters/FT/.gov...)" --> V1[supported<br/>rescue_tier=verified]
  Tier -- "non-allowlist + entity/number anchor" --> V2[supported<br/>rescue_tier=corroborated]
  Tier -- nothing --> Fail[unsupported]
  V1 --> Judge{7d. Source-judge<br/>v9 self-correcting<br/>3 verdicts}
  V2 --> Judge
  Pass1 --> Judge
  Judge -- supported --> Final[Render with citation]
  Judge -- "corrected (numeric/rank/temporal)" --> Patch[Patch prose inline<br/>with source value]
  Judge -- "unsupported (judge_rejected)" --> FailJ[unsupported]
  Patch --> Final
  Fail --> FQ[7e. Final qualify<br/>surgical rewrite]
  FailJ --> FQ
  FQ --> Final
  Final --> DB[(SQLite runs table)]
  DB --> History[/history page replay]

  classDef ok fill:#064e3b,stroke:#34d399,color:#d1fae5,stroke-width:1.5px
  classDef fail fill:#7c2d12,stroke:#fa552e,color:#fed7aa,stroke-width:1.5px
  classDef gate fill:#fa552e,stroke:#fdba8c,color:#fff,stroke-width:2px
  classDef store fill:#1e3a8a,stroke:#60a5fa,color:#dbeafe,stroke-width:1.5px
  class Pass1,V1,V2,Final,Patch ok
  class Fail,FailJ,FQ fail
  class Pool,Tier,Judge gate
  class DB,History store
`;

const MODELS = [
  { step: "Research synthesis", model: "mistral-medium-2604", temp: "0.2", note: "Dense extraction over parallel signals" },
  { step: "Industry label polish", model: "mistral-small-2603", temp: "0.1", note: "Customer-facing label, breadth-preserving" },
  { step: "Generation", model: "mistral-medium-2604", temp: "0.7", note: "12 candidates, ≥3 novel" },
  { step: "Scoring", model: "mistral-small-2603", temp: "0.2 → 0.4", note: "Self-consistency, two passes" },
  { step: "Per-candidate verification", model: "mistral-small-2603", temp: "0.1", note: "Tavily + duplicate-detection + grounding extraction" },
  { step: "Selection + enrichment", model: "mistral-large-2512", temp: "0.4", note: "Customer-ready prose" },
  { step: "Polish", model: "mistral-small-2603", temp: "0.1", note: "Full-pool excerpts; converts unanchored markers" },
  { step: "Meta-evaluate", model: "mistral-medium-2604", temp: "0.1", note: "Per-claim fact-check; atomic claim splitting (v7)" },
  { step: "Web-verify rescue", model: "(no LLM)", temp: "—", note: "Deterministic 2-tier credibility classifier" },
  { step: "Source judge (v7)", model: "mistral-small-2603", temp: "0.1", note: "Claim ↔ source coherence" },
  { step: "Final qualify (v7)", model: "mistral-small-2603", temp: "0.1", note: "Surgical rewrite of unsupported numerics/entities" },
  { step: "Embeddings", model: "mistral-embed", temp: "—", note: "Retrieval + diversity" },
];

type DataRow = { name: string; type: "PRESET" | "LIVE WEB" | "MIXED"; where: string; note: string };
const DATA_SOURCES: DataRow[] = [
  { name: "Wikipedia / Wikidata", type: "LIVE WEB", where: "Step 1 research", note: "Public REST APIs; full Wikipedia coverage" },
  { name: "Recent news", type: "LIVE WEB", where: "Step 1 research", note: "Tavily news topic, ~5 results, deep-read with selectolax" },
  { name: "Career pages", type: "LIVE WEB", where: "Step 1 research (depth=high)", note: "httpx + Playwright/Lightpanda fallback" },
  { name: "Existing AI initiatives", type: "LIVE WEB", where: "Step 1 research", note: "Tavily targeted searches" },
  { name: "Verified-companies index", type: "PRESET", where: "data/companies_raw.jsonl", note: "Hand-curated ~hundreds; rapidfuzz match; confidence boost only, never a gate" },
  { name: "Gap-fill", type: "LIVE WEB", where: "Step 1b", note: "1 Tavily query per missing field" },
  { name: "Precedent corpus (NARROW)", type: "PRESET", where: "data/precedents_raw.jsonl (~2,150 entries)", note: "Google Cloud customer stories + Evidently AI blueprints + Google Cloud blueprints. Every inspired_by ID has to live here." },
  { name: "Generation web_search tool", type: "LIVE WEB", where: "Step 3", note: "≤4 Tavily calls per generation" },
  { name: "Per-candidate verification", type: "LIVE WEB", where: "Step 5", note: "1 Tavily search + deep-read per top-K candidate" },
  { name: "Web-verify rescue", type: "LIVE WEB", where: "Step 7c", note: "≤12 Tavily searches per run" },
  { name: "Web-verify allowlist", type: "PRESET", where: "src/web_verify.py:_ALLOWLIST_DOMAINS", note: "~40 trusted domains + gov TLD suffixes; auditable" },
  { name: "Source judge", type: "MIXED", where: "Step 7d", note: "Re-reads already-fetched ledger entries; LLM judgment" },
  { name: "Cache", type: "PRESET", where: "data/cache/genai_usecases.db", note: "SQLite cache populated by past LIVE WEB calls" },
];

export default function Architecture() {
  return (
    <>
      <AnimatedBackground />
      <main className="relative z-10 min-h-screen px-4 sm:px-8 py-10 max-w-6xl mx-auto">
        <SiteNav />
        <header className="mb-10">
          <span className="text-[11px] uppercase tracking-[0.2em] text-mistral-orangeBright font-bold">
            Architecture · technical reference
          </span>
          <h1 className="mt-2 text-4xl sm:text-5xl font-bold text-white tracking-tight">
            Pipeline, models, evidence flow.
          </h1>
          <p className="mt-4 text-lg text-slate-300 leading-relaxed max-w-3xl">
            Every stage with its model, temperature, what it reads, what it
            writes, and where the data comes from. v7 adds an LLM source-judge
            and surgical post-verification rewrite — both visible below.
          </p>
        </header>

        {/* Pipeline diagram + determinism rules — side-by-side on wide
            screens so the right column isn't empty next to the LR mermaid.
            On narrow screens this stacks. */}
        <div className="grid grid-cols-1 lg:grid-cols-[2fr_1fr] gap-6 mb-8">
          <Section
            title="Full pipeline"
            subtitle="14 steps · activities decorated with Mistral Workflows determinism rules"
          >
            <MermaidDiagram source={PIPELINE_MERMAID} id="arch-pipeline" />
            <Legend
              items={[
                { color: "bg-mistral-orange", label: "LLM activity" },
                { color: "bg-blue-700", label: "Live web (Tavily / HTTP)" },
                { color: "bg-emerald-700", label: "Preset corpus / index" },
                { color: "bg-slate-600", label: "I/O" },
              ]}
            />
          </Section>
          <Section
            title="Determinism contract"
            subtitle="Why the workflow class is pure orchestration"
          >
            <ul className="text-sm text-slate-300 space-y-2.5 leading-relaxed">
              <li>
                <code className="text-mistral-orangeBright text-xs">src/workflow.py</code>
                {" "}has no <code className="text-xs">datetime.now()</code>, no
                {" "}<code className="text-xs">random()</code>, no I/O. The
                runtime replays workflow code from history; non-determinism
                breaks replay.
              </li>
              <li>
                Every side-effect — every LLM call, web fetch, DB read,
                embedding — lives in an activity with an explicit
                {" "}<code className="text-xs">start_to_close_timeout</code>.
              </li>
              <li>
                Every activity uses typed Pydantic I/O. Every LLM call uses
                {" "}<code className="text-xs">response_format</code> with
                a JSON schema. No prose-parsing downstream.
              </li>
              <li>
                Output type is{" "}
                <code className="text-mistral-orangeBright text-xs">ChatAssistantWorkflowOutput</code>
                {" "}so the workflow publishes as a Le Chat assistant.
              </li>
            </ul>
          </Section>
        </div>

        {/* Model + temperature table */}
        <Section title="Models and temperatures" subtitle="Locked per CLAUDE.md; deviations require explicit approval">
          <div className="overflow-x-auto rounded-lg border border-mistral-border">
            <table className="w-full text-sm">
              <thead className="bg-mistral-surfaceLight">
                <tr>
                  <Th>Step</Th>
                  <Th>Model</Th>
                  <Th>Temperature</Th>
                  <Th>Note</Th>
                </tr>
              </thead>
              <tbody>
                {MODELS.map((m, i) => (
                  <tr
                    key={m.step}
                    className={`${i % 2 ? "bg-mistral-surface/40" : ""} border-t border-mistral-border`}
                  >
                    <Td className="font-semibold text-white">{m.step}</Td>
                    <Td className="font-mono text-mistral-orangeBright text-xs">{m.model}</Td>
                    <Td className="font-mono text-xs">{m.temp}</Td>
                    <Td className="text-slate-300">{m.note}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        {/* Data source map */}
        <Section
          title="Data sources — preset vs live web"
          subtitle="Where every read happens and which sources are narrow/curated"
        >
          <div className="overflow-x-auto rounded-lg border border-mistral-border">
            <table className="w-full text-sm">
              <thead className="bg-mistral-surfaceLight">
                <tr>
                  <Th>Source</Th>
                  <Th>Type</Th>
                  <Th>Where</Th>
                  <Th>Note</Th>
                </tr>
              </thead>
              <tbody>
                {DATA_SOURCES.map((d, i) => (
                  <tr
                    key={d.name}
                    className={`${i % 2 ? "bg-mistral-surface/40" : ""} border-t border-mistral-border`}
                  >
                    <Td className="font-semibold text-white">{d.name}</Td>
                    <Td>
                      <TypeChip kind={d.type} />
                    </Td>
                    <Td className="font-mono text-xs text-slate-300">{d.where}</Td>
                    <Td className="text-slate-300">{d.note}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-xs text-ink-secondary mt-3">
            Two narrow places worth knowing about: the precedent corpus (~2,150
            entries — every <code className="text-mistral-orangeBright">inspired_by</code>{" "}
            ID has to be in here) and the verified-companies index (~hundreds, hand-curated,
            confidence boost only). Everywhere else the system reads is open web through
            Tavily / Wikipedia / direct HTTP.
          </p>
        </Section>

        {/* Evidence ledger flow */}
        <Section
          title="Evidence ledger flow"
          subtitle="One append-only typed object threads through every stage"
        >
          <MermaidDiagram source={LEDGER_MERMAID} id="arch-ledger" />
          <p className="text-sm text-slate-300 mt-3 leading-relaxed">
            Every external source the pipeline reads gets dropped into the ledger
            with a stable <code className="text-mistral-orangeBright">ev-{`<hash>`}</code>{" "}
            ID. Downstream stages cite these IDs instead of duplicating content.
            Meta-eval verifies claims against the entire pool; the v6 widening
            (every entry, not just the cited ones) was the fix that pushed
            fact-check pass rates from ~70% to 100%.
          </p>
        </Section>

        {/* v7 verification chain */}
        <Section
          title="Verification chain (v7)"
          subtitle="Every substantive claim runs the same gauntlet — pool → web-verify → judge → render"
        >
          <MermaidDiagram source={VERIFICATION_CHAIN_MERMAID} id="arch-verify" />
          <ul className="text-sm text-slate-300 mt-3 space-y-1.5 leading-relaxed">
            <li>
              <span className="font-semibold text-white">Pool check</span> — meta-eval
              first tries to anchor the claim against any ledger entry.
            </li>
            <li>
              <span className="font-semibold text-white">Web-verify rescue</span> —
              if the pool can't, one targeted Tavily search runs with a deterministic
              two-tier credibility gate.
            </li>
            <li>
              <span className="font-semibold text-white">Source judge</span> — a
              Mistral Small reads the (claim, snippet) pair and decides whether the
              source actually supports the claim, vs. just contains related entities.
              Catches the L'Oréal aiautomationglobal.com / BNP intuitionlabs.ai
              failure class from v6.
            </li>
            <li>
              <span className="font-semibold text-white">Final qualify</span> —
              anything still unsupported gets surgically rewritten into qualitative
              phrasing so the report doesn't ship fabricated facts. Replaces v6's
              pre-strip-at-polish behavior, which was over-stripping real claims.
            </li>
          </ul>
        </Section>

        {/* Determinism & workflow rules */}
        <footer className="mt-16 mb-6 pt-6 border-t border-mistral-border text-sm text-ink-muted text-center">
          Want the plain-English version?{" "}
          <a href="/how-it-works" className="text-mistral-orangeBright hover:text-mistral-orange">
            /how-it-works
          </a>{" "}
          · Want the prompts? See{" "}
          <code className="text-mistral-orangeBright">docs/prompts.md</code> in the repo.
        </footer>
      </main>
    </>
  );
}

function Section({
  title,
  subtitle,
  children,
}: {
  title: string;
  subtitle: string;
  children: React.ReactNode;
}) {
  return (
    <section className="glass rounded-2xl p-6 sm:p-8 mb-8">
      <span className="text-[11px] uppercase tracking-[0.2em] text-mistral-orangeBright font-bold">
        {subtitle}
      </span>
      <h2 className="mt-1 text-2xl font-bold text-white tracking-tight mb-5">
        {title}
      </h2>
      {children}
    </section>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="px-3 py-2 text-left text-[11px] uppercase tracking-[0.15em] text-ink-secondary font-bold">
      {children}
    </th>
  );
}

function Td({ children, className }: { children: React.ReactNode; className?: string }) {
  return <td className={`px-3 py-2 align-top ${className ?? ""}`}>{children}</td>;
}

function TypeChip({ kind }: { kind: "PRESET" | "LIVE WEB" | "MIXED" }) {
  const styles: Record<string, string> = {
    PRESET: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40",
    "LIVE WEB": "bg-blue-500/15 text-blue-300 border-blue-500/40",
    MIXED: "bg-purple-500/15 text-purple-300 border-purple-500/40",
  };
  return (
    <span
      className={`inline-block px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider rounded border ${styles[kind]}`}
    >
      {kind}
    </span>
  );
}

function Legend({ items }: { items: { color: string; label: string }[] }) {
  return (
    <div className="flex flex-wrap gap-3 mt-3 text-[11px] uppercase tracking-wider text-ink-secondary">
      {items.map((it) => (
        <span key={it.label} className="flex items-center gap-1.5">
          <span className={`w-2.5 h-2.5 rounded-sm ${it.color}`} />
          {it.label}
        </span>
      ))}
    </div>
  );
}
