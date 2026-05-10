"use client";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import AnimatedBackground from "../../components/AnimatedBackground";
import SiteNav from "../../components/SiteNav";

/**
 * /grounding/[runId] — explorable evidence database for one run.
 *
 * Reviewer flow: click a citation in a use case card → land on this page
 * → see exactly which sources the system grounded that claim against.
 * Each entry shows title, URL (clickable), source kind (Wikipedia /
 * Tavily / per-candidate-verification / web-verify rescue / claim-verify
 * judge / etc.), the step that fetched it, and an excerpt of the content.
 *
 * Filterable: per use case (only show citations that this use case
 * referenced), per source kind (drill into rescue layer specifically),
 * full-text search.
 */

type Entry = {
  id: string;
  source_kind: string;
  url: string | null;
  title: string;
  content: string;
  fetched_at_step: string;
  confidence: string | null;
};

type ByUseCase = {
  id: string;
  title: string;
  evidence_ids: string[];
  inspired_by: string[];
};

type GroundingSummary = {
  total_entries: number;
  by_kind: Record<string, number>;
  kinds_used: string[];
  kinds_not_used: string[];
};

type GroundingResponse = {
  run_id: string;
  company_name: string;
  entries: Entry[];
  by_use_case: ByUseCase[];
  summary?: GroundingSummary;
};

const API =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_URL) || "/api";

const SOURCE_KIND_LABEL: Record<string, { label: string; tone: string }> = {
  wikipedia: { label: "Wikipedia", tone: "bg-blue-500/15 text-blue-300 border-blue-500/40" },
  news: { label: "News", tone: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40" },
  tavily: { label: "Tavily", tone: "bg-cyan-500/15 text-cyan-300 border-cyan-500/40" },
  precedent: { label: "Precedent corpus", tone: "bg-purple-500/15 text-purple-300 border-purple-500/40" },
  jobs: { label: "Career page", tone: "bg-amber-500/15 text-amber-300 border-amber-500/40" },
  existing_initiative: { label: "Existing AI initiative", tone: "bg-orange-500/15 text-orange-300 border-orange-500/40" },
  company_verification: { label: "Company verify", tone: "bg-slate-500/15 text-slate-300 border-slate-500/40" },
  gap_fill: { label: "Gap-fill search", tone: "bg-pink-500/15 text-pink-300 border-pink-500/40" },
  generation_tool: { label: "Generation web search", tone: "bg-emerald-500/15 text-emerald-300 border-emerald-500/40" },
  per_candidate_verification: { label: "Per-candidate verify", tone: "bg-orange-500/15 text-orange-300 border-orange-500/40" },
  claim_verification: { label: "Web-verify rescue", tone: "bg-indigo-500/15 text-indigo-300 border-indigo-500/40" },
};

export default function GroundingPage() {
  const params = useParams<{ runId: string }>();
  const runId = params?.runId;
  const [data, setData] = useState<GroundingResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filterKind, setFilterKind] = useState<string>("all");
  const [filterUseCase, setFilterUseCase] = useState<string>("all");
  const [search, setSearch] = useState("");
  const [openIds, setOpenIds] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (!runId) return;
    fetch(`${API}/grounding/${runId}`)
      .then((r) => {
        if (!r.ok) throw new Error(`grounding fetch failed: ${r.status}`);
        return r.json();
      })
      .then((d: GroundingResponse) => setData(d))
      .catch((e: unknown) => setError(String(e)));
  }, [runId]);

  const entries = data?.entries ?? [];
  const byUseCase = data?.by_use_case ?? [];

  // Build a per-evidence-id list of use case titles that referenced it
  const refsByEv: Record<string, string[]> = {};
  for (const uc of byUseCase) {
    for (const eid of uc.evidence_ids) {
      (refsByEv[eid] ||= []).push(uc.title);
    }
  }

  const allowedIdsForUseCase: Set<string> | null =
    filterUseCase === "all"
      ? null
      : new Set(byUseCase.find((u) => u.id === filterUseCase)?.evidence_ids ?? []);

  const filtered = entries.filter((e) => {
    if (filterKind !== "all" && e.source_kind !== filterKind) return false;
    if (allowedIdsForUseCase && !allowedIdsForUseCase.has(e.id)) return false;
    if (search) {
      const s = search.toLowerCase();
      const blob = `${e.title} ${e.url ?? ""} ${e.content}`.toLowerCase();
      if (!blob.includes(s)) return false;
    }
    return true;
  });

  const counts: Record<string, number> = {};
  for (const e of entries) counts[e.source_kind] = (counts[e.source_kind] || 0) + 1;
  const kindOptions = ["all", ...Object.keys(counts).sort()];

  function toggleOpen(id: string) {
    setOpenIds((s) => {
      const next = new Set(s);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <>
      <AnimatedBackground />
      <main className="relative z-10 min-h-screen px-4 sm:px-8 py-10 max-w-5xl mx-auto">
        <SiteNav />
        <header className="mb-8">
          <span className="text-[11px] uppercase tracking-[0.2em] text-mistral-orangeBright font-bold">
            Grounding · evidence database
          </span>
          <h1 className="mt-2 text-4xl sm:text-5xl font-bold text-white tracking-tight">
            Every source the system grounded {data?.company_name ?? "the report"} against.
          </h1>
          <p className="mt-3 text-slate-300 max-w-3xl leading-relaxed">
            Each entry is one source the pipeline read at some point during
            the run. Use case citations (`Inspired by precedents`,
            `evidence_ids`) all point here. Click any entry to see its
            content excerpt; click the URL to open the original.
          </p>
        </header>

        {error && (
          <div className="glass border-l-4 border-bad rounded-lg p-4 mb-6">
            <p className="text-bad font-semibold">Couldn't load grounding data</p>
            <p className="text-ink-secondary text-sm font-mono mt-1">{error}</p>
          </div>
        )}

        {!data && !error && (
          <div className="glass rounded-lg p-6 text-center text-ink-secondary italic">
            Loading evidence ledger…
          </div>
        )}

        {data && entries.length === 0 && (
          <div className="glass rounded-lg p-6 text-center text-ink-secondary">
            No grounding entries available for this run.
          </div>
        )}

        {data && entries.length > 0 && (
          <>
            {/* Used / not-used overview — at-a-glance summary of which
                source kinds the pipeline drew from for this run. The
                "not used" list shows what was AVAILABLE but didn't fire
                (e.g. depth=low → no jobs / no news, or company didn't
                trigger gap-fill). */}
            {data.summary && (
              <div className="glass rounded-xl p-4 sm:p-5 mb-4">
                <h2 className="text-[11px] uppercase tracking-[0.18em] text-mistral-orangeBright font-bold mb-3">
                  Data sources used in this run
                </h2>
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <div className="text-[11px] uppercase tracking-wider text-ink-muted mb-1.5">
                      Used ({data.summary.kinds_used.length})
                    </div>
                    <div className="flex flex-wrap gap-1.5">
                      {data.summary.kinds_used.map((k) => {
                        const meta = SOURCE_KIND_LABEL[k];
                        const count = data.summary?.by_kind[k] ?? 0;
                        return (
                          <span
                            key={k}
                            className={`px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider rounded border ${meta?.tone || "bg-slate-500/15 text-slate-300 border-slate-500/40"}`}
                          >
                            {meta?.label || k}
                            <span className="ml-1.5 font-mono opacity-70">×{count}</span>
                          </span>
                        );
                      })}
                    </div>
                  </div>
                  {data.summary.kinds_not_used.length > 0 && (
                    <div>
                      <div className="text-[11px] uppercase tracking-wider text-ink-muted mb-1.5">
                        Available but didn't fire ({data.summary.kinds_not_used.length})
                      </div>
                      <div className="flex flex-wrap gap-1.5">
                        {data.summary.kinds_not_used.map((k) => (
                          <span
                            key={k}
                            className="px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider rounded border border-mistral-border text-ink-muted opacity-60"
                          >
                            {SOURCE_KIND_LABEL[k]?.label || k}
                          </span>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              </div>
            )}
            <div className="glass rounded-xl p-4 sm:p-5 mb-6 space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                <label className="block">
                  <span className="text-[11px] uppercase tracking-wider text-ink-muted">
                    Source kind
                  </span>
                  <select
                    value={filterKind}
                    onChange={(e) => setFilterKind(e.target.value)}
                    className="mt-1 w-full px-3 py-2 bg-mistral-dark/60 border border-mistral-border rounded text-white text-sm focus:border-mistral-orange focus:outline-none"
                  >
                    {kindOptions.map((k) => (
                      <option key={k} value={k}>
                        {k === "all" ? `All (${entries.length})` : `${SOURCE_KIND_LABEL[k]?.label || k} (${counts[k]})`}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-[11px] uppercase tracking-wider text-ink-muted">
                    Cited by use case
                  </span>
                  <select
                    value={filterUseCase}
                    onChange={(e) => setFilterUseCase(e.target.value)}
                    className="mt-1 w-full px-3 py-2 bg-mistral-dark/60 border border-mistral-border rounded text-white text-sm focus:border-mistral-orange focus:outline-none"
                  >
                    <option value="all">All use cases</option>
                    {byUseCase.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.title.slice(0, 60)}{u.title.length > 60 ? "…" : ""}
                      </option>
                    ))}
                  </select>
                </label>
                <label className="block">
                  <span className="text-[11px] uppercase tracking-wider text-ink-muted">
                    Full-text search
                  </span>
                  <input
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                    placeholder="title, URL, or content…"
                    className="mt-1 w-full px-3 py-2 bg-mistral-dark/60 border border-mistral-border rounded text-white text-sm focus:border-mistral-orange focus:outline-none"
                  />
                </label>
              </div>
              <div className="flex items-center gap-2 text-xs text-ink-secondary">
                Showing <span className="font-mono text-mistral-orangeBright">{filtered.length}</span>
                {" "}/ {entries.length} sources
              </div>
            </div>

            <div className="space-y-3">
              {filtered.map((e) => {
                const meta = SOURCE_KIND_LABEL[e.source_kind] || {
                  label: e.source_kind,
                  tone: "bg-slate-500/15 text-slate-300 border-slate-500/40",
                };
                const refs = refsByEv[e.id] ?? [];
                const isOpen = openIds.has(e.id);
                return (
                  <article
                    key={e.id}
                    className="glass rounded-lg border-l-4 border-mistral-orange/40 hover:border-mistral-orange transition-colors p-4 sm:p-5"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center flex-wrap gap-2 mb-1">
                          <span
                            className={`px-2 py-0.5 text-[11px] font-bold uppercase tracking-wider rounded border ${meta.tone}`}
                          >
                            {meta.label}
                          </span>
                          <code className="text-[11px] text-ink-muted font-mono">{e.id}</code>
                          <span className="text-[10px] uppercase tracking-wider text-ink-muted">
                            fetched at {e.fetched_at_step}
                          </span>
                          {e.confidence && (
                            <span className="text-[10px] uppercase tracking-wider text-ink-muted">
                              conf: {e.confidence}
                            </span>
                          )}
                        </div>
                        <h3 className="text-base font-semibold text-white truncate">{e.title}</h3>
                        {e.url && (
                          <a
                            href={e.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-xs text-mistral-orangeBright hover:text-mistral-orange break-all"
                          >
                            {e.url} ↗
                          </a>
                        )}
                        {refs.length > 0 && (
                          <div className="mt-1.5 flex flex-wrap items-center gap-1.5 text-[11px] text-ink-secondary">
                            <span className="text-ink-muted">cited by:</span>
                            {refs.map((t, i) => (
                              <span
                                key={i}
                                className="px-2 py-0.5 rounded bg-mistral-orange/10 border border-mistral-orange/30 text-mistral-orangeBright"
                              >
                                {t.slice(0, 50)}{t.length > 50 ? "…" : ""}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <button
                        onClick={() => toggleOpen(e.id)}
                        className="shrink-0 px-2.5 py-1 text-xs rounded border border-mistral-border hover:border-mistral-orange hover:text-white transition-colors text-ink-secondary"
                      >
                        {isOpen ? "Hide content" : "Show content"}
                      </button>
                    </div>
                    {isOpen && (
                      <div className="mt-3 pt-3 border-t border-mistral-border">
                        <pre className="text-[12.5px] text-slate-300 whitespace-pre-wrap break-words font-mono leading-relaxed max-h-[400px] overflow-y-auto bg-mistral-dark/40 border border-mistral-border rounded p-3">
                          {e.content || "(no content captured)"}
                        </pre>
                      </div>
                    )}
                  </article>
                );
              })}
            </div>
          </>
        )}
      </main>
    </>
  );
}
