"use client";
import { useTheme } from "./ThemeProvider";

/**
 * Light/dark theme toggle button. State + persistence + fade animation
 * live in ThemeProvider; this component is just the chip.
 */
export default function ThemeToggle() {
  const { theme, toggle } = useTheme();

  return (
    <button
      type="button"
      onClick={toggle}
      className="flex items-center gap-2 px-3 py-1.5 rounded-lg border border-mistral-border hover:border-mistral-orange transition-colors text-xs uppercase tracking-wider text-ink-secondary hover:text-white"
      aria-label={`Switch to ${theme === "dark" ? "light" : "dark"} mode`}
    >
      {theme === "dark" ? (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor"
          strokeWidth="1.5" strokeLinecap="square" shapeRendering="crispEdges" aria-hidden>
          <circle cx="7" cy="7" r="2.5" />
          <line x1="7" y1="1" x2="7" y2="2.5" />
          <line x1="7" y1="11.5" x2="7" y2="13" />
          <line x1="1" y1="7" x2="2.5" y2="7" />
          <line x1="11.5" y1="7" x2="13" y2="7" />
          <line x1="2.5" y1="2.5" x2="3.5" y2="3.5" />
          <line x1="10.5" y1="10.5" x2="11.5" y2="11.5" />
          <line x1="2.5" y1="11.5" x2="3.5" y2="10.5" />
          <line x1="10.5" y1="3.5" x2="11.5" y2="2.5" />
        </svg>
      ) : (
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor"
          strokeWidth="1.5" strokeLinecap="square" shapeRendering="crispEdges" aria-hidden>
          <path d="M11 8 A4.5 4.5 0 1 1 6 3 A3.5 3.5 0 0 0 11 8 Z" />
        </svg>
      )}
      <span>{theme === "dark" ? "Light" : "Dark"}</span>
    </button>
  );
}
