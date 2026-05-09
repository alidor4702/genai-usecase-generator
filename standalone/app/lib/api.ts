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
  id: string;
  step: string;
  actor: string;
  action: string;
  started_at: string;
  completed_at: string | null;
  duration_ms: number | null;
  inputs_summary: string | null;
  outputs_summary: string | null;
  error: string | null;
  // `running` flips false the moment we receive `step_complete` for the same id
  running?: boolean;
};

// Subset of the backend Report we actually consume in the UI. Backend may
// include more fields; we type only what the cards / footer renders.
export type TimeToValue = {
  estimate: string;
  anchored_to: string[];
  basis: "precedent" | "ballpark_assumption" | "unknown";
  rationale?: string | null;
};

export type EnrichedUseCase = {
  id: string;
  title: string;
  description: string;
  why_this_company: string;
  example_input: string;
  example_output: string;
  suggested_mistral_products: string[];
  blueprint_pattern:
    | "rag"
    | "agent_with_tools"
    | "document_ai_pipeline"
    | "fine_tuned_domain"
    | "hybrid_retrieval";
  blueprint_mermaid: string;
  time_to_value: TimeToValue;
  operating_cost_tier: "low" | "medium" | "high" | "unknown";
  impact_tier: "low" | "medium" | "high";
  complexity_tier: "low" | "medium" | "high";
  top_implementation_risk: string;
  inspired_by: string[];
  grounded_in: string[];
  evidence_ids: string[];
  builds_on_existing?: boolean;
  builds_on_note?: string | null;
};

export type FactCheckEntry = {
  claim: string;
  use_case_id: string;
  passed: boolean;
  rationale: string | null;
  rescue_tier?: "verified" | "corroborated" | null;
  rescue_url?: string | null;
  judge_rejected?: boolean;
  judge_reason?: string | null;
};

export type Report = {
  company: { identity: { name: string }; classification: { industry: string } };
  // Backend field is `top_use_cases` (Pydantic Report model). Earlier
  // versions of this type had `use_cases` which would break at runtime
  // the moment a report came back. Mirror the API shape exactly.
  top_use_cases: EnrichedUseCase[];
  rejected_appendix?: { title: string; one_line_reason: string }[];
  quality: {
    diversity: number;
    specificity_per_use_case: number[];
    fact_check: FactCheckEntry[];
  };
  meta_review?: {
    confidence: number;
    sales_engineer_ready: boolean;
    weakness_reason?: string | null;
    cross_cutting_concern?: string | null;
  } | null;
  intro_text?: string;
};

export type ReportResponse = {
  run_id: string;
  report: Report;
  markdown: string;
};

// Bypass Next.js rewrite proxy when NEXT_PUBLIC_API_URL is set — Next's
// dev rewrite buffers SSE responses (well-known issue), so the live event
// stream stalls until the run completes. Setting NEXT_PUBLIC_API_URL to
// the backend URL (e.g. http://localhost:8000) hits the FastAPI server
// directly. CORS on the backend is wide-open in dev so this just works.
const API =
  (typeof process !== "undefined" && process.env.NEXT_PUBLIC_API_URL) ||
  "/api";

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
  // Legacy unnamed SSE messages — keep for backward compat with older API
  es.addEventListener("message", (m) => {
    try {
      const data = JSON.parse((m as MessageEvent<string>).data);
      onEvent(data as TraceEvent);
    } catch {
      // ignore parse errors
    }
  });
  // Live feed: step_start fires the instant an activity begins (not when it
  // ends), so the UI can render a "running" card immediately. step_complete
  // fires when the activity finishes — caller merges by `id` to flip the
  // running card into a finished one with duration + outputs_summary.
  es.addEventListener("step_start", (m) => {
    try {
      const data = JSON.parse((m as MessageEvent<string>).data);
      onEvent({ ...data, running: true } as TraceEvent);
    } catch {
      // ignore
    }
  });
  es.addEventListener("step_complete", (m) => {
    try {
      const data = JSON.parse((m as MessageEvent<string>).data);
      onEvent({ ...data, running: false } as TraceEvent);
    } catch {
      // ignore
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
