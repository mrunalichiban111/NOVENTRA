// Types and helpers for the M5 persona arena routes (backend/app/api/personas.py).
// Everything here is SIMULATED: model-simulated customer reactions, never market research.
// Numbers (D, SE, counts) come straight from the API; nothing is computed here.

import { request } from "./api";

export interface Persona {
  id: string;
  name: string;
  segment: string;
  occupation: string;
  age: number;
  budget_tier_inr: string;
  fit: "target" | "adjacent" | "weak" | "non_target";
  urgency: number;
  skepticism: number;
  tech_comfort: number;
  hidden_objection: string;
  constraints: string[];
}

export interface Panel {
  business_id: string;
  panel_id: string | null;
  status: "none" | "pending" | "generating" | "ready" | "failed";
  error: string | null;
  count: number;
  personas: Persona[] | null;
  simulated: boolean;
  label: string;
}

export interface PersonaRunView {
  repeat: number;
  decision: "convert" | "bounce";
  reason: string;
  section: string;
  objection_type: string;
  top_objection: string;
}

export interface PersonaResult {
  persona_id: string;
  name: string | null;
  segment: string | null;
  fit: string | null;
  runs: PersonaRunView[];
  completed: number;
  convert_rate: number;
}

export interface Objection {
  objection_type: string;
  section: string;
  count: number;
  bounces: number;
  personas: number;
  example: string;
}

export interface Results {
  business_id: string;
  evaluation_id: string | null;
  site_version: number | null;
  status: "not_started" | "queued" | "running" | "complete" | "incomplete";
  completed_runs: number;
  persona_count: number;
  expected_runs: number;
  d_score: number | null;
  d_se: number | null;
  personas: PersonaResult[];
  objections: Objection[];
  friction_by_section: Record<string, number>;
  missing_info: { item: string; count: number }[];
  converted: number;
  error_code: string | null;
  error: string | null;
  retryable: boolean;
  note: string;
  simulated: boolean;
  label: string;
}

const base = (id: string) => `/businesses/${id}/personas`;

export const getPanel = (id: string) => request<Panel>(base(id));
export const getResults = async (id: string): Promise<Results> => {
  const r = await request<Results>(`${base(id)}/results`);
  return { ...r, personas: r.personas ?? [], objections: r.objections ?? [], missing_info: r.missing_info ?? [] };
};

// Writes: only ever called from an explicit click, never retried automatically.
export const generatePanel = (id: string) => request<Panel>(`${base(id)}/generate`, { method: "POST" });
export const evaluateSite = (id: string, siteVersion?: number) =>
  request<{ evaluation_id: string; status: string; changed: boolean }>(`${base(id)}/evaluate`, {
    method: "POST",
    body: JSON.stringify(siteVersion ? { site_version: siteVersion } : {}),
  });
