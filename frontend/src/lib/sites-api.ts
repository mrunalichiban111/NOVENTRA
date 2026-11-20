// Types and helpers for the M4B site routes (docs/api.md "Site deployment"). Shapes mirror the
// Pydantic models SiteOut, SiteVersionOut, DeploymentsList and DeployRequested in
// backend/app/api/schemas.py. Nothing is inferred or defaulted here.

import { request } from "./api";

// How far the deployment was verified. These are separate from `publicly_reachable`, and from
// the business reaching LIVE.
export type VerificationLevel = "simulated" | "local" | "public";

export interface SiteVersion {
  version: number;
  status: string; // draft, generating, deployed, failed, abandoned, ...
  content_hash: string | null;
  artifact_hash: string | null;
  parent_version: number | null;
  generation_attempt: number;
  error: string | null;
  deployed_at: string | null;
  created_at: string;
}

export interface Site {
  business_id: string;
  status: string;
  host: string | null;
  url: string | null; // only once a version is live
  live_version: number | null;
  verification_level: VerificationLevel | null;
  publicly_reachable: boolean; // true only for `public` verification
  versions: SiteVersion[];
}

export interface Deployment {
  id: string;
  version: number;
  attempt: number;
  host: string;
  provider: string;
  status: string; // started, activated, verified, failed
  verification_level: VerificationLevel | null;
  publicly_reachable: boolean;
  error: string | null;
  checks: Record<string, unknown>[]; // shape not specified by the API; not rendered
  started_at: string;
  finished_at: string | null;
}

export interface Deployments { business_id: string; deployments: Deployment[] }

// 202 when accepted; 200 with changed=false when already DEPLOYING or LIVE.
export interface DeployRequested {
  business_id: string;
  status: string;
  host: string | null;
  changed: boolean;
}

const base = (id: string) => `/businesses/${id}`;

// The contract says these arrays are always present; default them anyway so an incomplete
// response degrades to "no history" instead of crashing the page.
export const getSite = async (id: string): Promise<Site> => {
  const s = await request<Site>(`${base(id)}/site`);
  return { ...s, versions: s.versions ?? [] };
};
export const getDeployments = async (id: string): Promise<Deployments> => {
  const d = await request<Deployments>(`${base(id)}/deployments`);
  return { ...d, deployments: d.deployments ?? [] };
};

// A write: only ever called from an explicit user click, never retried automatically.
export const requestDeploy = (id: string) =>
  request<DeployRequested>(`${base(id)}/deploy`, { method: "POST" });
