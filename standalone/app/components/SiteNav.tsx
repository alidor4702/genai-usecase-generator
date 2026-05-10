"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import CompastralMark from "./CompastralMark";
import ThemeToggle from "./ThemeToggle";

/**
 * Top navigation bar — present on every page. Carries the brand wordmark,
 * links to the two explainer pages (non-technical /how-it-works and
 * technical /architecture), and the light/dark theme toggle.
 */
// /grounding is per-run, accessed via UseCaseCard citation chips —
// kept out of the top-nav since it requires a runId.
const NAV: { href: string; label: string }[] = [
  { href: "/", label: "Home" },
  { href: "/generate", label: "Generate" },
  { href: "/history", label: "History" },
  { href: "/how-it-works", label: "How it works" },
  { href: "/architecture", label: "Architecture" },
];

export default function SiteNav() {
  const path = usePathname();
  return (
    <nav className="flex items-center justify-between mb-8 gap-4">
      <Link href="/" className="flex items-center gap-2.5 group">
        <CompastralMark size={36} />
        <span className="text-base font-bold text-white tracking-tight">
          Compastral
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
