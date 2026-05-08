"use client";
import { useEffect, useRef, useState } from "react";

// Lazy-load mermaid only on the client (it touches `document`).
let mermaidPromise: Promise<typeof import("mermaid").default> | null = null;
function loadMermaid() {
  if (!mermaidPromise) {
    mermaidPromise = import("mermaid").then((m) => {
      m.default.initialize({ startOnLoad: false, theme: "dark" });
      return m.default;
    });
  }
  return mermaidPromise;
}

export default function MermaidDiagram({ source, id }: { source: string; id: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    loadMermaid()
      .then((mermaid) => mermaid.render(`mermaid-${id}`, source))
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
  return <div ref={ref} className="overflow-x-auto" />;
}
