"use client";
import AnimatedBackground from "../components/AnimatedBackground";
import PhaseIcon from "../components/PhaseIcon";
import SiteNav from "../components/SiteNav";

/**
 * /how-it-works — non-technical visual explainer.
 *
 * Audience: a Mistral sales engineer, a business reviewer, or anyone
 * who wants to understand WHAT the system does without reading code.
 * No prompts, no model names, no API references. Plain language and
 * illustrated cards.
 */
type Phase = {
  step: string;
  title: string;
  oneLine: string;
  body: string;
  reads: string[];
  produces: string[];
};

const PHASES: Phase[] = [
  {
    step: "research",
    title: "1. Research the company",
    oneLine: "Find out what the company actually does.",
    body:
      "The system reads four sources at once: Wikipedia, the most recent business news, the company's own job postings, and any public mentions of AI projects they already run. It writes one structured profile of the company — what they sell, where they operate, what data they sit on, what their stated priorities are, and what AI work they've already announced.",
    reads: ["Wikipedia + Wikidata", "Recent news (Tavily)", "Career pages", "Public AI announcements"],
    produces: ["Company profile", "Confidence score", "List of existing AI initiatives"],
  },
  {
    step: "retrieve",
    title: "2. Find peer precedents",
    oneLine: "Look up how similar companies have used GenAI.",
    body:
      "From a curated library of about 2,150 real GenAI deployments at other companies (Google Cloud customer stories, blueprint patterns), the system pulls the eight examples most similar to the target company's industry and data shape. These aren't templates — they're evidence the model uses to anchor what's realistic.",
    reads: ["Precedent library (~2,150 deployments)"],
    produces: ["Top 8 similar peer deployments"],
  },
  {
    step: "generate",
    title: "3. Brainstorm 12 ideas",
    oneLine: "Draft a wide pool, not a polished three.",
    body:
      "A generation model writes 12 candidate use cases. At least three are required to be NOVEL — not adaptations of any single precedent, but combinations or original directions. Each candidate cites which company-specific facts make it relevant. The model can run live web searches mid-draft when it needs more grounding.",
    reads: ["Company profile", "Peer precedents", "Hand-curated examples", "Live web (when needed)"],
    produces: ["12 candidates with reasoning"],
  },
  {
    step: "score",
    title: "4. Score against five criteria",
    oneLine: "Rate each idea on relevance, impact, feasibility, distinctiveness, and Mistral fit.",
    body:
      "Every candidate gets five scores from a calibrated rubric: how relevant is it to a core workflow at scale, how iconic and distinctive is it for THIS company specifically, how impactful is it for them, how feasible to ship in a typical engagement timeline, and how well does it lean into Mistral's strengths. To stay calibrated, the scoring runs twice with slightly different randomness and merges results.",
    reads: ["12 candidates", "Five scoring criteria"],
    produces: ["Aggregate score per candidate"],
  },
  {
    step: "verify",
    title: "5. Check what's already done",
    oneLine: "Make sure we're not proposing something the company already runs.",
    body:
      "The top candidates each get a targeted live web search: has this company already deployed this exact thing? If yes, the candidate is dropped and the next best one replaces it. If they've done something related but not identical, the proposal is kept with a 'builds on existing' note. While the verifier is reading those sources, it also extracts any concrete facts that could ground the customer-ready prose later.",
    reads: ["Live web search per candidate"],
    produces: ["Verified top 3", "Grounding facts for enrichment"],
  },
  {
    step: "enrich",
    title: "6. Write customer-ready prose",
    oneLine: "Turn the three picks into a deliverable.",
    body:
      "A premium model writes the final prose for each of the three: refined description, why-this-company explanation, an example user query and what the system would return, an architecture sketch, time-to-value estimate, top implementation risk, and which specific Mistral products would be involved. Numbers and named claims have to come from a cited source.",
    reads: ["Top 3 candidates", "All grounding evidence collected so far"],
    produces: ["3 customer-ready use case write-ups"],
  },
  {
    step: "review",
    title: "7. Review like a senior reviewer",
    oneLine: "Stress-test every claim before delivery.",
    body:
      "A senior-reviewer pass examines every substantive claim across the three use cases — would a sales engineer comfortably bring this to a customer meeting? Each factual assertion is marked supported (with a source) or unsupported. For unsupported ones, the system runs one more targeted web search ranked by source credibility, then a separate judge reads the result and decides if it actually backs the claim. Anything still unsupported gets surgically rewritten into qualitative phrasing so the report doesn't ship fabricated facts.",
    reads: ["Full evidence trail", "One last live web search per flagged claim"],
    produces: ["Sales-engineer-ready verdict", "Per-claim transparency"],
  },
];

export default function HowItWorks() {
  return (
    <>
      <AnimatedBackground />
      <main className="relative z-10 min-h-screen px-4 sm:px-8 py-10 max-w-5xl mx-auto">
        <SiteNav />
        <header className="mb-10">
          <span className="text-[11px] uppercase tracking-[0.2em] text-mistral-orangeBright font-bold">
            How it works · plain English
          </span>
          <h1 className="mt-2 text-4xl sm:text-5xl font-bold text-white tracking-tight">
            From a company name to three customer-ready GenAI use cases.
          </h1>
          <p className="mt-4 text-lg text-slate-300 leading-relaxed max-w-3xl">
            The system runs seven phases. Each phase has one job. Each phase reads
            something specific and produces something specific that the next phase
            uses. You can see every step happening live in the Generate page.
          </p>
        </header>

        <section className="space-y-5 mb-12">
          {PHASES.map((p, i) => (
            <PhaseCard key={p.step} phase={p} index={i} />
          ))}
        </section>

        <FlowDiagram />

        <FaqSection />

        <footer className="mt-16 mb-6 pt-6 border-t border-mistral-border text-sm text-ink-muted text-center">
          Want the technical version with prompts, models, and the full evidence
          chain? See{" "}
          <a href="/architecture" className="text-mistral-orangeBright hover:text-mistral-orange">
            /architecture
          </a>
          .
        </footer>
      </main>
    </>
  );
}

function PhaseCard({ phase, index }: { phase: Phase; index: number }) {
  return (
    <article
      className="glass rounded-2xl p-6 sm:p-7 border-l-4 border-mistral-orange/60 hover:border-mistral-orange transition-colors slide-in"
      style={{ animationDelay: `${index * 80}ms` }}
    >
      <div className="flex items-start gap-4">
        <div className="shrink-0 w-12 h-12 rounded-xl bg-gradient-to-br from-mistral-orange to-mistral-orangeBright flex items-center justify-center text-white shadow-lg shadow-mistral-orange/30">
          <PhaseIcon step={phase.step} />
        </div>
        <div className="flex-1 min-w-0">
          <h2 className="text-2xl font-bold text-white tracking-tight">{phase.title}</h2>
          <p className="text-mistral-orangeBright text-sm font-semibold mt-0.5 italic">
            {phase.oneLine}
          </p>
          <p className="text-slate-300 mt-3 leading-relaxed">{phase.body}</p>
          <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="rounded-lg p-3 bg-mistral-dark/50 border border-mistral-border">
              <div className="text-[11px] uppercase tracking-[0.18em] text-ink-muted font-bold mb-2">
                Reads
              </div>
              <ul className="space-y-1">
                {phase.reads.map((r) => (
                  <li key={r} className="text-sm text-slate-300 flex items-start gap-1.5">
                    <span className="text-mistral-orange">›</span>
                    {r}
                  </li>
                ))}
              </ul>
            </div>
            <div className="rounded-lg p-3 bg-mistral-dark/50 border border-mistral-border">
              <div className="text-[11px] uppercase tracking-[0.18em] text-ink-muted font-bold mb-2">
                Produces
              </div>
              <ul className="space-y-1">
                {phase.produces.map((r) => (
                  <li key={r} className="text-sm text-slate-300 flex items-start gap-1.5">
                    <span className="text-emerald-400">✓</span>
                    {r}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>
      </div>
    </article>
  );
}

function FlowDiagram() {
  return (
    <section className="glass rounded-2xl p-6 sm:p-8 mb-12">
      <span className="text-[11px] uppercase tracking-[0.2em] text-mistral-orangeBright font-bold">
        At a glance
      </span>
      <h2 className="mt-1 text-2xl font-bold text-white tracking-tight">
        Information flow, no jargon
      </h2>
      <p className="text-ink-secondary mt-2 max-w-3xl">
        Each block is one phase. The arrow shows what the next phase reads.
      </p>
      <div className="mt-6 grid grid-cols-1 md:grid-cols-7 gap-2">
        {PHASES.map((p) => (
          <div
            key={p.step}
            className="text-center p-3 rounded-lg bg-mistral-dark/40 border border-mistral-border hover:border-mistral-orange transition-colors"
          >
            <div className="w-8 h-8 mx-auto rounded-md bg-mistral-orange/15 text-mistral-orangeBright flex items-center justify-center mb-2">
              <PhaseIcon step={p.step} />
            </div>
            <div className="text-xs font-bold text-white uppercase tracking-wider">
              {p.title.replace(/^\d+\.\s+/, "")}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

function FaqSection() {
  const faqs: { q: string; a: string }[] = [
    {
      q: "What does the system NOT do?",
      a: "It doesn't propose use cases for AI vendors themselves (Mistral, OpenAI, Anthropic) — when the target IS the AI provider, the framing breaks down. It also won't propose anything the company has already deployed; that gets caught and either dropped or marked 'builds on existing'.",
    },
    {
      q: "How does it avoid making stuff up?",
      a: "Every factual claim has to be supported by a source the system actually retrieved — Wikipedia, news, Tavily, an evidence ledger built up across the run. Anything the system can't anchor is either rescued via one more targeted web search (with credibility tiers), judged by a separate model, or rewritten into qualitative phrasing. The final report shows a per-claim transparency block with chips like [verified ↗] and [judge: rejected] so the reviewer can audit the chain.",
    },
    {
      q: "Why three use cases, not five or ten?",
      a: "Three is what a Mistral sales engineer can pitch in a single customer meeting. Twelve are generated under the hood; the top three after scoring + verification + de-duplication land in the deliverable. The other nine are kept in a 'rejected appendix' with one-line reasons.",
    },
    {
      q: "How long does a run take?",
      a: "About 2-4 minutes depending on research depth. Every step is observable live — you can watch the agents think, search, and write in real time on the Generate page.",
    },
    {
      q: "Where can it go wrong?",
      a: "If a company has very little public signal (tiny private firm, sparse Wikipedia), the system refuses gracefully instead of fabricating. If a relevant peer deployment isn't in the precedent corpus, the model anchors on a weaker match — solvable by widening the corpus over time. If the verification chain still rejects a real claim, the surgical rewriter qualifies the language so the report stays defensible.",
    },
  ];
  return (
    <section className="glass rounded-2xl p-6 sm:p-8">
      <span className="text-[11px] uppercase tracking-[0.2em] text-mistral-orangeBright font-bold">
        FAQ
      </span>
      <h2 className="mt-1 text-2xl font-bold text-white tracking-tight">
        Common questions
      </h2>
      <div className="mt-5 space-y-4">
        {faqs.map((f) => (
          <details key={f.q} className="group">
            <summary className="cursor-pointer text-base font-semibold text-white hover:text-mistral-orangeBright transition-colors">
              {f.q}
            </summary>
            <p className="mt-2 text-slate-300 leading-relaxed pl-1">{f.a}</p>
          </details>
        ))}
      </div>
    </section>
  );
}
