// Workspace stages and eligibility, derived from the business the API returned. This only
// decides what to show: the backend stays authoritative and its 409 responses win.

import type { Business, Status, Verdict } from "./api";

export const PASSING_VERDICTS: readonly Verdict[] = ["go", "go_with_changes"];

// Statuses where the pipeline is working and the page should keep polling.
export const POLLING_STATUSES: ReadonlySet<Status> = new Set<Status>([
  "INTAKE", "PREMORTEM", "DEPLOYING", "TESTING", "IMPROVING",
]);

const ORDER: Status[] = [
  "INTAKE", "PREMORTEM", "GATED", "DOMAIN_PENDING", "DEPLOYING", "LIVE", "TESTING", "IMPROVING", "DONE",
];

// GATED with a passing verdict: the only state in which names can be generated.
export function isEligibleForLaunchPlan(b: Business): boolean {
  return b.status === "GATED" && b.verdict !== null && PASSING_VERDICTS.includes(b.verdict);
}

// How far the pipeline got; a FAILED business counts as the stage it failed in.
function reached(b: Business): number {
  if (b.status === "REJECTED") return -1;
  const stage = b.status === "FAILED" ? b.pipeline.failed_stage : b.status;
  return stage ? ORDER.indexOf(stage) : -1;
}

// What the Naming Studio may do for this business. `edit`: generate, check, claim (GATED with a
// passing verdict, or DOMAIN_PENDING where a claim can be replaced). `readonly`: a name was
// claimed and the pipeline moved on. `blocked`: names are not available.
export type NamingMode = "edit" | "readonly" | "blocked";

export function namingMode(b: Business): NamingMode {
  if (b.status === "DOMAIN_PENDING" || isEligibleForLaunchPlan(b)) return "edit";
  return reached(b) >= ORDER.indexOf("DOMAIN_PENDING") ? "readonly" : "blocked";
}

// What the Website Launch Studio can show. `ready`: a name is claimed (business.domain is set).
// `no_claim`: eligible but nothing claimed yet. `blocked`: no website path for this business.
export type WebsiteMode = "ready" | "no_claim" | "blocked";

export function websiteMode(b: Business): WebsiteMode {
  if (b.status !== "REJECTED" && b.domain && reached(b) >= ORDER.indexOf("DOMAIN_PENDING")) return "ready";
  return isEligibleForLaunchPlan(b) ? "no_claim" : "blocked";
}

export type StageKey = "assessment" | "names" | "website" | "personas" | "improve";

// The persona arena tests a live website: a claimed host and a business that reached LIVE.
export function arenaAvailable(b: Business): boolean {
  return b.status !== "REJECTED" && !!b.domain && reached(b) >= ORDER.indexOf("LIVE");
}

export interface StageState {
  key: StageKey;
  label: string;
  href: string | null; // null: not navigable (locked or upcoming)
  upcoming: boolean;
  note: string | null; // why it is locked, or "Upcoming"
}

export function stageStates(b: Business | null, id: string): StageState[] {
  const base = `/b/${id}`;
  const past = b ? reached(b) : -1;
  const claimAt = ORDER.indexOf("DOMAIN_PENDING");

  let namesLock: string | null = null;
  let websiteLock: string | null = null;
  if (!b) {
    namesLock = websiteLock = "Loading";
  } else if (b.status === "REJECTED") {
    namesLock = websiteLock = "Not available for a rejected idea";
  } else if (past >= claimAt) {
    // A name was claimed or later stages ran; both studios stay reachable.
  } else if (isEligibleForLaunchPlan(b)) {
    websiteLock = "Choose a name first";
  } else if (b.verdict === "rethink") {
    namesLock = websiteLock = "Locked by the Rethink verdict";
  } else if (b.status === "FAILED") {
    namesLock = websiteLock = "Analysis failed";
  } else {
    namesLock = websiteLock = "Available after the verdict";
  }

  return [
    { key: "assessment", label: "Assessment", href: base, upcoming: false, note: null },
    { key: "names", label: "Naming Studio", href: namesLock ? null : `${base}/names`, upcoming: false, note: namesLock },
    { key: "website", label: "Website Launch", href: websiteLock ? null : `${base}/website`, upcoming: false, note: websiteLock },
    {
      key: "personas",
      label: "Persona Arena",
      href: b && arenaAvailable(b) ? `${base}/personas` : null,
      upcoming: false,
      note: !b ? "Loading" : arenaAvailable(b) ? null : "Available once your website is live",
    },
    { key: "improve", label: "Improvement", href: null, upcoming: true, note: "Upcoming" },
  ];
}
