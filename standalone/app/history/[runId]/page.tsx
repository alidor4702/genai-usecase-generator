"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useParams } from "next/navigation";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import AnimatedBackground from "../../components/AnimatedBackground";
import MermaidDiagram from "../../components/MermaidDiagram";
import SiteNav from "../../components/SiteNav";
import UseCaseCard from "../../components/UseCaseCard";
import type { Report } from "../../lib/api";

/**
 * /history/[runId] — re-open a persisted run as a full structured
 * report. Renders the same UseCaseCard + transparency block as the
 * Generate page does after a fresh run, just from the persisted
 * Report JSON instead of a live in-memory state.
 */

type RunDetail = {
  run_id: string;
  company_name: string;
  status: "completed" | "refused" | "failed";
  started_at: number;
  completed_at: number;
  fact_check_pass_rate: number | null;
  meta_eval_confidence: number | null;
  sales_engineer_ready: number | null;
  refusal_reason: string | null;
  error: string | null;
  report_markdown: string | null;
  report: Report | null;
};

const API =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_URL) || "/api";

function stripUseCaseSection(md: string): string {
  const lines = md.split("\n");
  let dropping = false;
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (line.startsWith("## ")) {
      if (dropping) dropping = false;
      const lookahead = lines.slice(i + 1, i + 40).join("\n");
      if (/^### /m.test(lookahead) && /use case/i.test(line)) {
        dropping = true;
        continue;
      }
    }
    if (dropping) continue;
    out.push(line);
  }
  return out.join("\n");
}

function hash(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) h = (h << 5) - h + s.charCodeAt(i);
  return String(Math.abs(h));
}

export default function HistoryDetailPage() {
  const params = useParams<{ runId: string }>();
  const runId = params?.runId;
  const [data, setData] = useState<RunDetail | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!runId) return;
    fetch(`${API}/runs/${runId}`)
      .then((r) => {
        if (!r.ok) throw new Error(`run fetch failed: ${r.status}`);
        return r.json();
      })
      .then((d: RunDetail) => setData(d))
      .catch((e: unknown) => setError(String(e)));
  }, [runId]);

  return (
    <>
      <AnimatedBackground />
      <main className="relative z-10 min-h-screen px-4 sm:px-8 py-10 max-w-6xl mx-auto">
        <SiteNav />

        <div className="mb-6 flex items-baseline justify-between">
          <Link
            href="/history"
            className="text-xs text-ink-secondary hover:text-mistral-orangeBright transition-colors"
          >
            ← Back to history
          </Link>
          {data && (
            <p className="text-xs text-ink-muted font-mono">
              run id <span className="text-ink-primary">{data.run_id}</span>
            </p>
          )}
        </div>

        {error && (
          <div className="glass border-l-4 border-bad rounded-lg p-4 mb-6">
            <p className="text-bad font-semibold">Couldn't load run</p>
            <p className="text-ink-secondary text-sm font-mono mt-1">{error}</p>
          </div>
        )}

        {!data && !error && (
          <div className="glass rounded-lg p-6 text-center text-ink-secondary italic">
            Loading run…
          </div>
        )}

        {data && (
          <>
            <header className="mb-8">
              <span className="text-[11px] uppercase tracking-[0.2em] text-mistral-orangeBright font-bold">
                {new Date(data.started_at * 1000).toLocaleString()}
              </span>
              <h1 className="mt-2 text-3xl sm:text-4xl font-bold text-white tracking-tight">
                {data.company_name}
              </h1>
            </header>

            {data.status === "refused" && data.refusal_reason && (
              <div className="glass border-l-4 border-warn rounded-lg p-4 mb-6">
                <p className="text-warn font-semibold mb-1">Refused to generate</p>
                <p className="text-ink-primary text-sm">{data.refusal_reason}</p>
              </div>
            )}
            {data.status === "failed" && (
              <div className="glass border-l-4 border-bad rounded-lg p-4 mb-6">
                <p className="text-bad font-semibold mb-1">Run failed</p>
                <p className="text-ink-primary text-sm font-mono">{data.error}</p>
              </div>
            )}

            {data.report && Array.isArray(data.report.top_use_cases) && data.report.top_use_cases.length > 0 && (
              <section className="space-y-4">
                <div className="flex items-baseline justify-between">
                  <h2 className="text-sm uppercase tracking-[0.18em] text-ink-secondary font-bold">
                    Generated use cases
                  </h2>
                  <span className="text-xs text-ink-muted">
                    {data.report.top_use_cases.length} customer-ready proposals
                  </span>
                </div>
                <div className="space-y-4">
                  {data.report.top_use_cases.map((uc, i) => (
                    <UseCaseCard key={uc.id ?? i} uc={uc} index={i} runId={data.run_id} />
                  ))}
                </div>
              </section>
            )}

            {data.report_markdown && (
              <article className="glass rounded-2xl p-6 sm:p-8 prose-mistral max-w-none mt-6">
                <ReactMarkdown
                  remarkPlugins={[remarkGfm]}
                  components={{
                    code(props) {
                      const { className, children, ...rest } = props as {
                        className?: string;
                        children?: React.ReactNode;
                      };
                      const match = /language-mermaid/.exec(className || "");
                      if (match) {
                        const src = String(children ?? "").trim();
                        return <MermaidDiagram source={src} id={hash(src)} />;
                      }
                      return (
                        <code className={className} {...rest}>
                          {children}
                        </code>
                      );
                    },
                  }}
                >
                  {data.report
                    ? stripUseCaseSection(data.report_markdown)
                    : data.report_markdown}
                </ReactMarkdown>
              </article>
            )}
          </>
        )}
      </main>
    </>
  );
}
