// Types and helpers for the M4A names routes (docs/api.md "Business names"). Shapes mirror the
// backend Pydantic models in backend/app/api/schemas.py; nothing is computed or defaulted here.

import { request } from "./api";

export type DomainStatus = "AVAILABLE" | "UNAVAILABLE" | "UNKNOWN";

export interface DomainCheck {
  domain: string;
  tld: string;
  status: DomainStatus;
  reason: string | null; // for UNKNOWN: not_configured, timeout, rate_limited, ...
  message: string; // safe display text from the backend
  raw_price: string | null; // registrar raw value, units unverified; deliberately not displayed
  currency: string | null;
  definitive: boolean | null;
  checked_at: string | null;
  cached?: boolean;
}

export interface ScoreFactor { value: number; weight: number; points: number }

export interface NameCandidate {
  id: string;
  name: string;
  slug: string | null;
  rank: number | null; // null for invalid candidates
  score_total: string | null; // Decimal serialised as a string, 0-100
  score_factors: Record<string, ScoreFactor>;
  valid: boolean;
  invalid_reason: string | null;
  rationale: string | null;
  subdomain_available: boolean;
  subdomain_host: string | null;
  selected: boolean;
  domain_checks: DomainCheck[];
}

export interface NamesList {
  business_id: string;
  status: string;
  subdomains_configured: boolean;
  generations_used: number;
  generations_max: number;
  disclaimer: string;
  candidates: NameCandidate[];
}

export interface NamesGenerated extends NamesList { run_id: string; replayed: boolean }

export interface AvailabilityResult {
  candidate_id: string;
  slug: string;
  results: DomainCheck[];
  disclaimer: string;
}

export interface NameSelected {
  business_status: string;
  subdomain_host: string | null;
  changed: boolean;
  replaced_slug: string | null;
  candidate: NameCandidate;
}

// Only the top five ranked valid candidates can be domain-checked (backend TOP_N).
export const AVAILABILITY_TOP_N = 5;

const base = (id: string) => `/businesses/${id}/names`;

// Same defensive defaults as the sites API: missing arrays become empty, never a crash.
const normalizeCandidate = (c: NameCandidate): NameCandidate => ({
  ...c,
  score_factors: c.score_factors ?? {},
  domain_checks: c.domain_checks ?? [],
});
const normalizeList = <T extends NamesList>(l: T): T => ({ ...l, candidates: (l.candidates ?? []).map(normalizeCandidate) });

export const getNames = async (id: string) => normalizeList(await request<NamesList>(base(id)));

// The Idempotency-Key makes a double click or a retry after a lost response return the stored
// result instead of using up another of the three generations.
export const generateNames = async (id: string, idempotencyKey: string) =>
  normalizeList(
    await request<NamesGenerated>(`${base(id)}/generate`, {
      method: "POST",
      body: "{}",
      headers: { "Idempotency-Key": idempotencyKey },
    }),
  );

export const checkAvailability = async (id: string, candidateId: string) => {
  const r = await request<AvailabilityResult>(`${base(id)}/${candidateId}/availability`, { method: "POST" });
  return { ...r, results: r.results ?? [] };
};

export const selectName = (id: string, candidateId: string, replace: boolean) =>
  request<NameSelected>(`${base(id)}/${candidateId}/select?replace=${replace}`, { method: "POST" });
