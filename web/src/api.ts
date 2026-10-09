// Typed client for the FastAPI backend (server/app.py).

export type RunStatus = "queued" | "running" | "completed" | "failed" | "stopped" | "interrupted";
export type RunKind = "benchmark" | "custom";

export type LlmStatusKind = "ok" | "credits_exhausted" | "auth_error";

export interface LlmStatus {
  status: LlmStatusKind;
  message?: string;
  since?: number;
}

export interface Health {
  status: string;
  api_key_configured: boolean;
  model: string;
  mcp_tools: number;
  mcp_transport: string | null;
  llm: LlmStatus;
}

export interface CityInfo {
  name: string;
  hotels: number;
  activities: number;
  restaurants: number;
  accessible_hotels: number;
}

export interface Catalog {
  origins: string[];
  destinations: CityInfo[];
  routes: [string, string][];
  interest_suggestions: string[];
  disruptions: Record<string, string>;
}

export interface RunSummary {
  id: string;
  created_at: number;
  started_at: number | null;
  finished_at: number | null;
  status: RunStatus;
  kind: RunKind;
  task_name: string | null;
  title: string;
  difficulty: string;
  judge_requested: number;
  error: string | null;
  error_kind: string | null;
  success: number | null;
  total_tokens: number | null;
  api_calls: number | null;
  cost_usd: number | null;
  judge_score: number | null;
  judge_passed: number | null;
}

export interface BenchmarkTask {
  name: string;
  task_id: string;
  difficulty: string;
  title: string;
  origin_city: string;
  destination_cities: string[];
  days: number;
  budget_max: number;
  party_size: number;
  dynamic_events: string[];
  latest_run: RunSummary | null;
}

export interface Violation {
  rule: string;
  category: string;
  message: string;
  booking_ids: string[];
  severity: "error" | "warning";
}

export interface LiveBooking {
  booking_id: string;
  type: string;
  name: string;
  date: string | null;
  time: string | null;
  end_date: string | null;
  cost: number;
}

export interface LiveState {
  bookings: LiveBooking[];
  budget_used: number;
  budget_max: number | null;
  party_size: number;
  requirements: { component: string; matched: number; required: number }[];
  violations: Violation[];
  usage: {
    api_calls: number;
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    estimated_cost_usd: number | null;
    tool_calls: number;
    tool_errors: number;
  };
}

export interface LlmCall {
  turn: number | null;
  purpose: string;
  input_tokens: number;
  output_tokens: number;
  latency_s: number;
}

export interface RunMetadata {
  model: string;
  api_calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  estimated_cost_usd: number | null;
  llm_latency_s: number;
  tool_calls: number;
  tool_errors: number;
  tool_calls_by_name: Record<string, number>;
  tool_errors_by_name: Record<string, number>;
  llm_calls: LlmCall[];
  time_elapsed: number;
}

export interface JudgeDimension {
  score: number;
  max_score: number;
  pct: number | null;
  reasoning: string;
  findings: string[];
}

export interface JudgeResult {
  overall_score: number;
  passed: boolean;
  pass_threshold: number;
  judge_model: string;
  dimensions: Record<string, JudgeDimension>;
  usage: { api_calls: number; total_tokens: number; estimated_cost_usd: number | null; input_tokens: number; output_tokens: number };
}

export interface RunRecord {
  success: boolean;
  final_itinerary_present: boolean;
  planning_requirements_satisfied: boolean;
  hard_constraints_satisfied: boolean;
  success_criteria_satisfied: boolean;
  constraints_valid: boolean;
  constraint_violations: Violation[];
  operation_log: { turn: number; tool: string; action: string; booking_id: string | null; summary: string }[];
  tracking_valid: boolean;
  tracking_problems: string[];
  metadata: RunMetadata;
  judge?: JudgeResult;
  judge_error?: { kind: string | null; message: string } | null;
  llm_error?: { kind: string; message: string; detail: string } | null;
  conversation?: { role: string; content: string }[];
}

export interface RunDetail extends RunSummary {
  task: Record<string, any>;
  record: RunRecord | null;
  itinerary: string | null;
}

export interface TierStats {
  tasks: number;
  success_rate: number;
  constraint_valid_rate: number;
  tracking_valid: boolean;
  avg_api_calls: number | null;
  avg_tool_calls: number | null;
  avg_total_tokens: number | null;
  total_tokens: number;
  total_cost_usd: number | null;
  avg_time_s: number | null;
  judge_avg_score: number | null;
  judge_pass_rate: number | null;
}

export interface Stats {
  benchmark: { by_difficulty: Record<string, TierStats>; overall: TierStats } | null;
  totals: { runs: number; tokens: number; api_calls: number; cost_usd: number; completed: number; succeeded: number };
}

export interface CustomTripInput {
  title?: string;
  origin_city: string;
  destinations: { city: string; nights: number }[];
  departure_date: string;
  budget_max: number;
  party_size: number;
  interests: string[];
  preferences: string[];
  wheelchair_accessible: boolean;
  min_activities: number;
  min_restaurants: number;
  disruption: string;
}

export interface RunEvent {
  id: number;
  type: string;
  ts: number;
  data: Record<string, any>;
}

export class ApiError extends Error {
  constructor(public status: number, message: string, public problems: string[] = []) {
    super(message);
  }
}

/** Backend origin. Empty means same origin (local dev proxy, or the API serving the built app). */
export const API_BASE = (import.meta.env.VITE_API_URL ?? "").replace(/\/$/, "");

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    let message = `${response.status} ${response.statusText}`;
    let problems: string[] = [];
    try {
      const body = await response.json();
      const detail = body.detail;
      if (typeof detail === "string") message = detail;
      else if (detail?.message) {
        message = detail.message;
        problems = detail.problems ?? [];
      } else if (Array.isArray(detail)) {
        problems = detail.map((d: any) => `${(d.loc ?? []).slice(1).join(".")}: ${d.msg}`);
        message = "Please fix the highlighted fields.";
      }
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(response.status, message, problems);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

const post = (body?: unknown): RequestInit => ({ method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const api = {
  health: () => request<Health>("/api/health"),
  catalog: () => request<Catalog>("/api/catalog"),
  tasks: () => request<BenchmarkTask[]>("/api/tasks"),
  runs: (kind?: RunKind) => request<RunSummary[]>(`/api/runs${kind ? `?kind=${kind}` : ""}`),
  run: (id: string) => request<RunDetail>(`/api/runs/${id}`),
  stats: () => request<Stats>("/api/stats"),
  startBenchmark: (taskName: string, judge: boolean) => request<{ id: string }>("/api/runs", post({ task_name: taskName, judge })),
  startCustom: (custom: CustomTripInput, judge: boolean) => request<{ id: string }>("/api/runs", post({ custom, judge })),
  stop: (id: string) => request<{ stopping: boolean }>(`/api/runs/${id}/stop`, post()),
  remove: (id: string) => request<void>(`/api/runs/${id}`, { method: "DELETE" }),
};

export const TERMINAL: RunStatus[] = ["completed", "failed", "stopped", "interrupted"];
