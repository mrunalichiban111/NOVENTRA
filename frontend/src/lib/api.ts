// Types and fetch helpers for the NOVENTRA API. Every number shown in the UI comes from these
// responses; nothing is computed here.

export type Status =
  | "INTAKE" | "PREMORTEM" | "GATED" | "DOMAIN_PENDING" | "DEPLOYING" | "LIVE"
  | "TESTING" | "IMPROVING" | "DONE" | "REJECTED" | "FAILED";

export type Verdict = "go" | "go_with_changes" | "rethink";

export type AssumptionKey =
  | "unit_price" | "variable_cost_per_unit" | "fixed_monthly_cost"
  | "startup_cost" | "monthly_visitors" | "visitor_to_order_conversion";

export interface Assumption {
  key: AssumptionKey;
  low: number;
  base: number;
  high: number;
  rationale: string | null;
  source: "llm" | "user" | "panel";
}

export interface HistoryEntry {
  id: number;
  actor: string;
  action: string;
  reason: string | null;
  from_status: string | null;
  to_status: string | null;
  ts: string;
}

export interface Pipeline {
  current_stage: Status;
  next_stage: Status | null;
  terminal: boolean;
  in_progress: boolean;
  attempts: number;
  max_attempts: number;
  failed_stage: Status | null;
  last_error: string | null;
  retryable: boolean;
  history: HistoryEntry[];
}

export interface Severity { severity: number; justification: string }

export interface PreMortem {
  target_customer: string;
  competitors: { name: string; verified: boolean; label: string }[];
  failure_reasons: string[];
  compliance_flags: { category: string; note: string; verify: boolean; verification_note: string }[];
  risk_severities: Record<string, Severity>;
  suggested_pivots: string[];
  disclaimer: string;
}

export interface Scorecard {
  version: number;
  stage: string;
  economics: number;
  risk: number;
  overall: number;
  verdict: Verdict;
  explanation: string[];
  next_actions: { text: string; source: string }[];
  risk_severities: Record<string, Severity>;
  notes: string[];
}

export interface ScenarioResult {
  contribution_margin: number;
  contribution_margin_pct: number | null;
  monthly_orders: number;
  monthly_operating_profit: number;
  six_month_cash: number;
  break_even_units: number | null;
  break_even_month: number | null;
  runway_months: number | null;
  runway_status: "self_sustaining" | "finite" | "beyond_horizon" | "underfunded";
  cash_negative_at_start: boolean;
  warnings: string[];
}

export interface Finance {
  starting_budget: number;
  starting_budget_source: string;
  scenarios: Record<"pessimistic" | "base" | "optimistic", ScenarioResult>;
  monte_carlo: { seed: number; n: number; p_profitable: number; p10: number; p50: number; p90: number };
  seed: number;
}

export interface Business {
  id: string;
  idea_text: string;
  market: string;
  // Decimal fields are serialised as strings by the API.
  price_guess: string | null;
  starting_budget: string | null;
  target_customer: string | null;
  status: Status;
  // The claimed NOVENTRA subdomain once a name is selected (M4A), else null.
  domain: string | null;
  created_at: string;
  updated_at: string;
  pipeline: Pipeline;
  rejection_reason: string | null;
  premortem: PreMortem | null;
  assumptions: Assumption[];
  finance: Finance | null;
  scorecard: Scorecard | null;
  verdict: Verdict | null;
  verdict_explanation: string[];
}

export interface NewIdea {
  idea_text: string;
  market?: string;
  price_guess?: number;
  starting_budget?: number;
}

export interface ContactMessage {
  name: string;
  email: string;
  message: string;
  website?: string;
}

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly code: string | null = null) {
    super(message);
  }
}

// Error bodies come in three shapes: `detail: "text"` (businesses routes), `detail: [{msg}]`
// (validation) and `detail: {code, message}` (names and sites routes).
async function parseError(res: Response): Promise<{ message: string; code: string | null }> {
  if (res.status === 429) {
    return { message: "Too many requests. Please wait a minute and try again.", code: "rate_limited" };
  }
  try {
    const body = await res.json();
    const d = body.detail;
    if (typeof d === "string") return { message: d, code: null };
    if (Array.isArray(d)) {
      const message = d.map((e: { msg?: string }) => (e.msg ?? "Invalid input").replace(/^Value error, /, "")).join(" ");
      return { message, code: null };
    }
    if (d && typeof d === "object" && typeof d.message === "string") {
      return { message: d.message, code: typeof d.code === "string" ? d.code : null };
    }
  } catch {
    // fall through
  }
  return { message: `Request failed (${res.status}).`, code: null };
}

// `path` is relative to /api, e.g. "/businesses/<id>/names".
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`/api${path}`, {
      ...init,
      cache: "no-store",
      headers: { "Content-Type": "application/json", ...init?.headers },
    });
  } catch {
    // fetch rejects with a bare TypeError ("Failed to fetch") when the server is unreachable.
    // Status 0 means no response was received, so the request may or may not have been processed.
    throw new ApiError("Could not reach the server. Check your connection and try again.", 0, "network");
  }

  if (!res.ok) {
    const { message, code } = await parseError(res);
    throw new ApiError(message, res.status, code);
  }
  return (await res.json()) as T;
}

export const submitContact = (contact: ContactMessage) =>
  request<{ status: string }>("/public/contact", {
    method: "POST",
    body: JSON.stringify(contact),
  });

const call = <T>(path: string, init?: RequestInit) => request<T>(`/businesses${path}`, init);

export const createBusiness = (idea: NewIdea) =>
  call<{ id: string; status: Status }>("", { method: "POST", body: JSON.stringify(idea) });

export const getBusiness = (id: string) => call<Business>(`/${id}`);

export const retryBusiness = (id: string) => call<Business>(`/${id}/retry`, { method: "POST" });

export type AssumptionEdit = { key: AssumptionKey; low?: number; base?: number; high?: number };

export const patchAssumptions = (id: string, assumptions: AssumptionEdit[]) =>
  call<Business>(`/${id}/assumptions`, { method: "PATCH", body: JSON.stringify({ assumptions }) });
