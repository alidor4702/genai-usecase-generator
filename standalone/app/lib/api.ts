// Thin client for the FastAPI backend. All paths are relative — Next.js
// rewrites /api/* to the backend (configured in next.config.mjs).

export type FocusArea = "general" | "operations" | "customer" | "sustainability";
export type ResearchDepth = "low" | "medium" | "high";

export type CriteriaWeights = {
  relevance: number;
  iconic_potential: number;
  estimated_impact: number;
  feasibility: number;
  mistral_suitability: number;
};

export type GenerateRequest = {
  company_name: string;
  focus_area: FocusArea;
  weights?: CriteriaWeights;
  research_depth: ResearchDepth;
};

export type StatusResponse = {
  run_id: string;
  company_name: string;
  status: "queued" | "running" | "completed" | "failed" | "refused";
  current_step: string;
  progress_percent: number;
  started_at: string | null;
  completed_at: string | null;
  error: string | null;
  refusal_reason: string | null;
  event_count: number;
  fact_check_pass_rate: number | null;
  meta_eval_confidence: number | null;
};

export type TraceEvent = {
  step: string;
  actor: string;
  action: string;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  inputs_summary: string | null;
  outputs_summary: string | null;
  error: string | null;
};

export type ReportResponse = {
  run_id: string;
  report: unknown;
  markdown: string;
};

const API = "/api"; // Next rewrites /api/* to FastAPI

export async function postGenerate(req: GenerateRequest): Promise<{ run_id: string }> {
  const r = await fetch(`${API}/generate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(req),
  });
  if (!r.ok) throw new Error(`generate failed: ${r.status}`);
  return r.json();
}

export async function getStatus(runId: string): Promise<StatusResponse> {
  const r = await fetch(`${API}/status/${runId}`);
  if (!r.ok) throw new Error(`status failed: ${r.status}`);
  return r.json();
}

export async function getReport(runId: string): Promise<ReportResponse> {
  const r = await fetch(`${API}/report/${runId}`);
  if (!r.ok) throw new Error(`report failed: ${r.status}`);
  return r.json();
}

// Subscribe to SSE event stream. Returns a cleanup function that closes
// the EventSource. Caller wires onEvent / onProgress / onDone.
export function subscribeToEvents(
  runId: string,
  onEvent: (e: TraceEvent) => void,
  onProgress: (p: { step: string; progress: number }) => void,
  onDone: (status: string) => void,
  onError?: (e: Event) => void,
): () => void {
  const es = new EventSource(`${API}/events/${runId}`);
  es.addEventListener("message", (m) => {
    try {
      const data = JSON.parse((m as MessageEvent<string>).data);
      onEvent(data as TraceEvent);
    } catch {
      // ignore parse errors
    }
  });
  es.addEventListener("progress", (m) => {
    try {
      const data = JSON.parse((m as MessageEvent<string>).data);
      onProgress(data);
    } catch {
      // ignore
    }
  });
  es.addEventListener("done", (m) => {
    onDone((m as MessageEvent<string>).data);
    es.close();
  });
  if (onError) es.onerror = onError;
  return () => es.close();
}
