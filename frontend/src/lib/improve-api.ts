// Types and helpers for the M6 improvement routes (backend/app/api/improve.py). D scores are
// SIMULATED and come straight from the API; nothing is computed here.

import { request } from "./api";

export type Classification = "SAFE_AUTOMATIC" | "FOUNDER_INPUT_REQUIRED" | "APPROVAL_REQUIRED";

export interface Opportunity {
  id: string;
  objection_type: string;
  section: string;
  evidence_count: number;
  bounces: number;
  personas: number;
  example: string;
  classification: Classification;
}

export interface Question {
  id: string;
  field_key: string;
  question: string;
  answer_type: string;
  required: boolean;
  opportunity_id: string | null;
}

export interface Answer { value: string | null; status: "answered" | "not_decided"; source: "user" }

export interface ProposalItem {
  id: string;
  path: string;
  section: string;
  old: string;
  new: string;
  rationale: string;
  classification: Classification;
  decision: "pending" | "approved" | "rejected";
}

export interface EvalSummary {
  evaluation_id: string | null;
  site_version: number | null;
  status: string | null;
  d_score: number | null;
  d_se: number | null;
  completed_runs: number | null;
  expected_runs: number | null;
}

export interface Attempt {
  business_id: string;
  attempt_id: string | null;
  status: string;
  reason: string | null;
  opportunities: Opportunity[];
  questions: Question[];
  answers: Record<string, Answer>;
  proposal: { items?: ProposalItem[]; rejected?: { path: string; reason: string }[]; classification?: Classification };
  source_version: number | null;
  draft_version: number | null;
  original: EvalSummary | null;
  draft: EvalSummary | null;
  delta_demand: number | null;
  threshold: number;
  promote_requested: boolean;
  note: string;
  simulated: boolean;
  label: string;
}

const base = (id: string) => `/businesses/${id}`;
const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) });

export const getAttempt = (id: string) => request<Attempt>(`${base(id)}/improve`);
export const startAttempt = (id: string) => post<Attempt>(`${base(id)}/improve/start`);
export const answerQuestion = (id: string, qid: string, answer: string | null, notDecided: boolean) =>
  request<Attempt>(`${base(id)}/improve/questions/${qid}`, {
    method: "PATCH",
    body: JSON.stringify(notDecided ? { not_decided: true } : { answer }),
  });
export const generateProposal = (id: string) => post<Attempt>(`${base(id)}/improve/proposal`);
export const approveItems = (id: string, itemIds: string[], approveConsequential: boolean) =>
  post<Attempt>(`${base(id)}/improve/approve`, { item_ids: itemIds, approve_consequential: approveConsequential });
export const rejectProposal = (id: string) => post<Attempt>(`${base(id)}/improve/reject`);
export const discardDraft = (id: string) => post<Attempt>(`${base(id)}/improve/discard`);
export const promoteDraft = (id: string) => post<Attempt>(`${base(id)}/improve/promote`);
export const rollbackSite = (id: string, toVersion: number) =>
  post<{ status: string }>(`${base(id)}/site/rollback`, { to_version: toVersion });
