"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import AnimatedBackground from "./components/AnimatedBackground";
import CompassDial from "./components/CompassDial";
import SiteNav from "./components/SiteNav";

/**
 * Landing page.
 *
 * Three things stack visually:
 *   1. Interactive 8-bit compass — drag to rotate, click N/E/S/W to
 *      snap, Spin for fun. When the user isn't touching it, the needle
 *      keeps rotating slowly from wherever it was last left.
 *   2. Typewriter "Compastral" wordmark — characters reveal one at a
 *      time. Loops once on mount.
 *   3. Tagline + Start button → /generate; secondary nav.
 */

const FULL_WORD = "Compastral";

function Typewriter({ text }: { text: string }) {
  const [shown, setShown] = useState(0);
  useEffect(() => {
    if (shown >= text.length) return;
    const t = window.setTimeout(() => setShown((s) => s + 1), 110);
    return () => window.clearTimeout(t);
  }, [shown, text.length]);
  return (
    <span className="font-bold tracking-tight">
      {text.slice(0, shown)}
      <span className="inline-block w-[3px] h-[0.85em] bg-mistral-orange ml-0.5 align-baseline animate-pulse" />
    </span>
  );
}

export default function Landing() {
  return (
    <>
      <AnimatedBackground />
      <main className="relative z-10 min-h-screen px-4 sm:px-8 py-10 max-w-5xl mx-auto">
        <SiteNav />

        <section className="flex flex-col items-center text-center pt-8 pb-12">
          <CompassDial size={280} showReadout={false} showControls={false} />
          <div className="text-[11px] uppercase tracking-[0.25em] text-mistral-orangeBright font-bold mt-8">
            Mistral Proto · Take-home
          </div>
          <h1 className="mt-4 text-6xl sm:text-7xl text-white leading-none">
            <Typewriter text={FULL_WORD} />
          </h1>
          <p className="mt-3 text-ink-secondary text-lg">
            <span className="italic">company × Mistral</span> · pronounced compass
          </p>

          <p className="mt-8 text-slate-300 text-lg max-w-2xl leading-relaxed">
            Three customer-ready GenAI use cases for any company. Grounded in
            2,150+ real peer deployments, fact-checked against live web sources,
            self-correcting on contradictions. Built on Mistral Workflows.
          </p>

          <div className="mt-10 flex items-center gap-3 flex-wrap justify-center">
            <Link
              href="/generate"
              className="px-8 py-3.5 bg-gradient-to-r from-mistral-orange to-mistral-orangeBright hover:from-mistral-orangeBright hover:to-mistral-orange text-white rounded-xl font-bold tracking-wide shadow-xl shadow-mistral-orange/30 hover:shadow-mistral-orange/50 transition-all"
            >
              Start →
            </Link>
            <Link
              href="/how-it-works"
              className="px-6 py-3.5 border-2 border-mistral-border hover:border-mistral-orange text-ink-secondary hover:text-white rounded-xl font-semibold uppercase tracking-wider text-sm transition-all"
            >
              How it works
            </Link>
            <Link
              href="/history"
              className="px-6 py-3.5 border-2 border-mistral-border hover:border-mistral-orange text-ink-secondary hover:text-white rounded-xl font-semibold uppercase tracking-wider text-sm transition-all"
            >
              History
            </Link>
          </div>
        </section>

        {/* What it does — three concise cards */}
        <section className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-12">
          <FeatureCard
            title="Grounded"
            body="Every fact in the report is anchored to a retrieved source. Wikipedia, news, Tavily live web, plus a curated 2,150-entry precedent corpus."
          />
          <FeatureCard
            title="Self-correcting"
            body="A source-judge step inspects every (claim, source) pair. Real catches stay flagged; numerical contradictions get rewritten with the source's actual value."
          />
          <FeatureCard
            title="Transparent"
            body="Per-claim verdicts visible in the report — supported · rescued · corrected · qualitatively rewritten. The reviewer sees the whole chain."
          />
        </section>

        <footer className="text-center text-sm text-ink-muted py-8 border-t border-mistral-border">
          <p>
            Compastral · 2026 ·
            <a
              href="https://github.com/alidor4702/genai-usecase-generator"
              target="_blank"
              rel="noreferrer"
              className="ml-1 text-mistral-orangeBright hover:text-mistral-orange"
            >
              source on GitHub ↗
            </a>
          </p>
        </footer>
      </main>
    </>
  );
}

function FeatureCard({ title, body }: { title: string; body: string }) {
  return (
    <article className="glass rounded-xl p-5 border-l-4 border-mistral-orange/40 hover:border-mistral-orange transition-colors">
      <h3 className="text-base font-bold text-white mb-2">{title}</h3>
      <p className="text-sm text-slate-300 leading-relaxed">{body}</p>
    </article>
  );
}
