"use client";
import { createContext, useCallback, useContext, useEffect, useState } from "react";

/**
 * ThemeProvider — light/dark mode with persistence across page navigations.
 *
 * Persistence strategy: a "compastral_theme" cookie. CLAUDE.md hard-don't
 * is on localStorage / sessionStorage specifically (not supported in the
 * artifact rendering env). Cookies are fine and survive client-side
 * navigation across routes within the standalone app, plus full reloads.
 *
 * Toggle behaviour: when the user clicks the toggle, the page does a
 * smooth color-fade transition (CSS) — see globals.css `.theme-fade`.
 * The fade is purely cosmetic; the theme attribute flips instantly.
 *
 * Public API:
 *   const { theme, setTheme, toggle } = useTheme();
 */

export type Theme = "dark" | "light";

const ThemeCtx = createContext<{
  theme: Theme;
  setTheme: (t: Theme) => void;
  toggle: () => void;
}>({
  theme: "dark",
  setTheme: () => {},
  toggle: () => {},
});

const COOKIE = "compastral_theme";

function readCookie(): Theme | null {
  if (typeof document === "undefined") return null;
  const match = document.cookie.match(new RegExp("(^|; )" + COOKIE + "=([^;]*)"));
  if (!match) return null;
  const v = decodeURIComponent(match[2]);
  return v === "light" || v === "dark" ? v : null;
}

function writeCookie(theme: Theme) {
  if (typeof document === "undefined") return;
  // 1-year cookie. Path=/ so every page sees it.
  document.cookie = `${COOKIE}=${theme}; path=/; max-age=${60 * 60 * 24 * 365}; samesite=lax`;
}

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setThemeState] = useState<Theme>("dark");
  const [hydrated, setHydrated] = useState(false);

  // Hydration: read cookie + set attribute on <html>. Doing this in an
  // effect avoids a server/client mismatch since the cookie is per-browser.
  useEffect(() => {
    const stored = readCookie();
    if (stored) setThemeState(stored);
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (!hydrated) return;
    const html = document.documentElement;
    html.setAttribute("data-theme", theme);
    // Briefly enable a CSS fade so the swap reads as a smooth transition
    // rather than a hard flash. We add the class, then remove after the
    // transition's natural end so subsequent style changes (hover, etc.)
    // don't all animate.
    html.classList.add("theme-fade");
    const timer = window.setTimeout(() => html.classList.remove("theme-fade"), 600);
    return () => window.clearTimeout(timer);
  }, [theme, hydrated]);

  const setTheme = useCallback((t: Theme) => {
    setThemeState(t);
    writeCookie(t);
  }, []);
  const toggle = useCallback(() => {
    setThemeState((cur) => {
      const next: Theme = cur === "dark" ? "light" : "dark";
      writeCookie(next);
      return next;
    });
  }, []);

  return (
    <ThemeCtx.Provider value={{ theme, setTheme, toggle }}>{children}</ThemeCtx.Provider>
  );
}

export function useTheme() {
  return useContext(ThemeCtx);
}
