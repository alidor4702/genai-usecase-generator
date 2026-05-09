"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import ThemeToggle from "./ThemeToggle";

/**
 * Top navigation bar — present on every page. Carries the brand wordmark,
 * links to the two explainer pages (non-technical /how-it-works and
 * technical /architecture), and the light/dark theme toggle.
 */
const NAV: { href: string; label: string }[] = [
  { href: "/", label: "Generate" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/architecture", label: "Architecture" },
];

export default function SiteNav() {
  const path = usePathname();
  return (
    <nav className="flex items-center justify-between mb-8 gap-4">
      <Link href="/" className="flex items-center gap-2.5 group">
        <span className="w-7 h-7 rounded bg-gradient-to-br from-mistral-orange to-mistral-orangeBright flex items-center justify-center shadow-md shadow-mistral-orange/40">
          <svg width="14" height="14" viewBox="0 0 14 14" stroke="white" strokeWidth="1.5"
            fill="none" strokeLinecap="square" shapeRendering="crispEdges" aria-hidden>
            {/* simplified Mistral M-glyph: 4 vertical bars stepping */}
            <line x1="2" y1="11" x2="2" y2="3" />
            <line x1="5" y1="11" x2="5" y2="5" />
            <line x1="8" y1="11" x2="8" y2="5" />
            <line x1="11" y1="11" x2="11" y2="3" />
          </svg>
        </span>
        <span className="text-base font-bold text-white tracking-tight">
          Use Case Generator
        </span>
      </Link>
      <div className="flex items-center gap-1.5">
        {NAV.map((n) => {
          const active = path === n.href || (n.href !== "/" && path?.startsWith(n.href));
          return (
            <Link
              key={n.href}
              href={n.href}
              className={`px-3 py-1.5 rounded-lg text-xs uppercase tracking-wider font-semibold transition-colors ${
                active
                  ? "bg-mistral-orange/15 text-mistral-orangeBright border border-mistral-orange/30"
                  : "border border-transparent text-ink-secondary hover:text-white hover:border-mistral-border"
              }`}
            >
              {n.label}
            </Link>
          );
        })}
        <span className="ml-1.5 hidden sm:block">
          <ThemeToggle />
        </span>
      </div>
    </nav>
  );
}
