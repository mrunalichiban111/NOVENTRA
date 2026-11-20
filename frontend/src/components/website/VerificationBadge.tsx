import styles from "./website.module.css";

// Verification level and public reachability are two separate facts, shown separately by the
// callers. This badge only names the level. A business reaching LIVE never implies "public".
const LEVELS: Record<string, { label: string; text: string; cls: string }> = {
  simulated: { label: "SIMULATED", text: "Simulated: a mock check. Nothing real was published or checked.", cls: styles.vSimulated },
  local: { label: "LOCAL", text: "Local: the release files were checked on the server. This says nothing about whether the site is reachable from the internet.", cls: styles.vLocal },
  public: { label: "PUBLIC", text: "Public: the address resolved, and fetching it over HTTPS returned exactly this release.", cls: styles.vPublic },
};

export default function VerificationBadge({ level }: { level: string | null }) {
  const info = level ? LEVELS[level] : undefined;
  const label = info?.label ?? (level ? "UNRECOGNISED" : "NOT VERIFIED");
  const cls = info?.cls ?? styles.vNone;
  return (
    <span className={`${styles.vBadge} ${cls}`} aria-label={`Verification level: ${label.toLowerCase()}`}>
      {label}
    </span>
  );
}

export const verificationText = (level: string | null): string =>
  (level && LEVELS[level]?.text) || (level ? `Unrecognised verification level “${level}”.` : "No release has been verified yet.");

// Public reachability explained on its own, independent of the level above.
export function reachabilityText(level: string | null, reachable: boolean): string {
  if (reachable && level === "public") return "Yes. A public check fetched this release from its address over HTTPS.";
  if (reachable) return "The server reports the site as reachable, but not with public verification. Treat this as unconfirmed.";
  if (level === "public") return "Not reported as reachable, although the level is public. Treat this as unconfirmed.";
  if (level === "local") return "No. Local verification does not test the internet-facing address.";
  if (level === "simulated") return "No. Nothing real was published, so nothing can be reached.";
  return "Not confirmed.";
}

// The API only sets publicly_reachable for `public` verification. Any other combination is
// contradictory data, and the UI refuses to treat it as a public site.
export const levelContradictsReachability = (level: string | null, reachable: boolean): boolean =>
  (reachable && level !== "public") || (!reachable && level === "public");
