// Small display helpers for the website studio. Missing values stay visibly missing.

export const formatTime = (iso: string | null | undefined): string => {
  if (!iso) return "—";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString();
};

const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, " ");

// Site-version statuses (backend SiteVersionStatus). Unknown values are shown as returned.
const VERSION_STATUS: Record<string, string> = {
  generating: "Generating content",
  draft: "Content ready",
  built: "Built, not yet deployed",
  deployed: "Deployed",
  failed: "Failed",
  abandoned: "Interrupted",
  superseded: "Superseded",
  rolled_back: "Rolled back",
};
export const versionStatusLabel = (s: string) => VERSION_STATUS[s] ?? titleCase(s);

const ATTEMPT_STATUS: Record<string, string> = {
  started: "Started",
  activated: "Activated, awaiting verification",
  verified: "Verified",
  failed: "Failed",
};
export const attemptStatusLabel = (s: string) => ATTEMPT_STATUS[s] ?? titleCase(s);

// What a release is relative to the one currently serving traffic. `current` is only ever the
// version the API names as `live_version`; nothing is inferred from dates or order.
export type ReleaseRole = "current" | "previous" | "failed" | "unfinished" | "other";

export function releaseRole(status: string, version: number, liveVersion: number | null): ReleaseRole {
  if (liveVersion !== null && version === liveVersion) return "current";
  if (status === "failed") return "failed";
  if (status === "deployed" || status === "superseded" || status === "rolled_back") return "previous";
  if (status === "generating" || status === "draft" || status === "built" || status === "abandoned") return "unfinished";
  return "other";
}
