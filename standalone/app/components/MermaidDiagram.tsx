"use client";
import { useEffect, useRef, useState } from "react";

/**
 * Mermaid diagram renderer with a Mistral-themed palette and node-type
 * styling injected at render time so blueprints feel like architecture
 * rather than boxes-and-arrows.
 *
 * Theme:
 *  - Mistral orange primary, deep cream secondary in light mode (via
 *    CSS variables) and slate-200 on charcoal in dark mode.
 *  - Node-type styling: any node whose body matches /llm|model/i gets
 *    a hex-shape highlight; /db|store|index|cache/i gets a cylinder
 *    accent; /user|customer|client/i gets a soft stadium pill. We do
 *    this by appending mermaid `classDef` + `class` lines to the body
 *    before render.
 */
let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid(theme: "dark" | "light") {
  // Re-initialize on theme change. mermaid keeps a singleton internally;
  // initialize() resets it so subsequent renders pick up the new theme.
  return import("mermaid").then((m) => {
    m.default.initialize({
      startOnLoad: false,
      theme: theme === "dark" ? "dark" : "neutral",
      themeVariables:
        theme === "dark"
          ? {
              primaryColor: "#1e2530",
              primaryBorderColor: "#fa552e",
              primaryTextColor: "#e6edf3",
              lineColor: "#fa552e",
              tertiaryColor: "#0a0e14",
              fontFamily: "ui-sans-serif, system-ui, sans-serif",
            }
          : {
              primaryColor: "#fdf6ec",
              primaryBorderColor: "#fa552e",
              primaryTextColor: "#1b1f2a",
              lineColor: "#fa552e",
              tertiaryColor: "#fffcf7",
              fontFamily: "ui-sans-serif, system-ui, sans-serif",
            },
    });
    return m.default;
  });
}

/**
 * Heuristically classify each node by its label, then emit classDef +
 * class lines so Mermaid renders LLM nodes / data stores / users
 * differently. This is on top of the per-pattern color the backend
 * already applies via _decorate_mermaid in src/ui/render.py.
 */
function decorateWithNodeTypes(body: string): string {
  const NODE_RE = /^\s*([A-Za-z_][\w]*)\s*[\[\(\{]([^\]\)\}]*)[\]\)\}]/gm;
  const llm = /\b(llm|model|gpt|mistral|small|medium|large|claude|embed)\b/i;
  const store = /\b(db|database|store|index|cache|corpus|lake|warehouse|vector)\b/i;
  const user = /\b(user|customer|client|analyst|engineer|operator)\b/i;
  const tool = /\b(api|service|tool|search|tavily|wiki|rest|http)\b/i;

  const llmIds: string[] = [];
  const storeIds: string[] = [];
  const userIds: string[] = [];
  const toolIds: string[] = [];

  for (const m of body.matchAll(NODE_RE)) {
    const id = m[1];
    const label = m[2];
    if (llm.test(label)) llmIds.push(id);
    else if (store.test(label)) storeIds.push(id);
    else if (user.test(label)) userIds.push(id);
    else if (tool.test(label)) toolIds.push(id);
  }

  const lines: string[] = [];
  if (llmIds.length) {
    lines.push("classDef nt_llm fill:#fa552e,stroke:#fdba8c,color:#fff,stroke-width:2px");
    lines.push(`class ${[...new Set(llmIds)].join(",")} nt_llm`);
  }
  if (storeIds.length) {
    lines.push("classDef nt_store fill:#1e3a8a,stroke:#60a5fa,color:#dbeafe,stroke-width:2px");
    lines.push(`class ${[...new Set(storeIds)].join(",")} nt_store`);
  }
  if (userIds.length) {
    lines.push("classDef nt_user fill:#064e3b,stroke:#34d399,color:#d1fae5,stroke-width:2px");
    lines.push(`class ${[...new Set(userIds)].join(",")} nt_user`);
  }
  if (toolIds.length) {
    lines.push("classDef nt_tool fill:#7c2d12,stroke:#fdba74,color:#fed7aa,stroke-width:1.5px");
    lines.push(`class ${[...new Set(toolIds)].join(",")} nt_tool`);
  }
  if (!lines.length) return body;
  return body + "\n" + lines.join("\n");
}

export default function MermaidDiagram({ source, id }: { source: string; id: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    const theme: "dark" | "light" =
      typeof document !== "undefined" &&
      document.documentElement.getAttribute("data-theme") === "light"
        ? "light"
        : "dark";
    const decorated = decorateWithNodeTypes(source);
    loadMermaid(theme)
      .then((mermaid) => mermaid.render(`mermaid-${id}-${theme}`, decorated))
      .then(({ svg }) => {
        if (alive && ref.current) ref.current.innerHTML = svg;
      })
      .catch((e: unknown) => {
        if (alive) setError(String(e));
      });
    return () => {
      alive = false;
    };
  }, [source, id]);

  if (error) {
    return (
      <pre className="text-xs text-red-400 bg-mistral-surface p-2 rounded">
        Mermaid render failed: {error}
      </pre>
    );
  }
  return (
    <div className="space-y-2">
      <div ref={ref} className="overflow-x-auto" />
      <Legend />
    </div>
  );
}

function Legend() {
  return (
    <div className="flex flex-wrap gap-3 text-[10px] uppercase tracking-wider text-ink-muted px-1">
      <span className="flex items-center gap-1">
        <span className="w-2 h-2 rounded-sm bg-mistral-orange" />
        LLM
      </span>
      <span className="flex items-center gap-1">
        <span className="w-2 h-2 rounded-sm bg-blue-600" />
        Data store
      </span>
      <span className="flex items-center gap-1">
        <span className="w-2 h-2 rounded-sm bg-emerald-700" />
        User / actor
      </span>
      <span className="flex items-center gap-1">
        <span className="w-2 h-2 rounded-sm bg-orange-800" />
        Tool / API
      </span>
    </div>
  );
}
