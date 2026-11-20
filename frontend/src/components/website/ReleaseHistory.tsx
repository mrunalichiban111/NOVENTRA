import type { Deployment, Site, SiteVersion } from "@/lib/sites-api";
import ui from "../ui.module.css";
import { attemptStatusLabel, formatTime, releaseRole, versionStatusLabel, type ReleaseRole } from "./format";
import VerificationBadge from "./VerificationBadge";
import styles from "./website.module.css";

// Only fields the API returns are shown. Hashes, storage details and the `checks` payload
// (shape unspecified) are intentionally not displayed. There are no rollback controls: the
// backend has no rollback endpoint in M4B.
const ROLE_LABEL: Record<ReleaseRole, string | null> = {
  current: "Current release",
  previous: "Previous release",
  failed: "Failed, not serving",
  unfinished: "Not deployed",
  other: null,
};
const ROLE_CLASS: Record<ReleaseRole, string> = {
  current: styles.pillGood,
  previous: "",
  failed: styles.pillBad,
  unfinished: styles.pillWarn,
  other: "",
};

export default function ReleaseHistory({ site, deployments }: { site: Site | null; deployments: Deployment[] }) {
  const records = new Map<number, SiteVersion>((site?.versions ?? []).map((v) => [v.version, v]));
  // Attempts can reference a version the site record did not return. Show them rather than
  // dropping them, and say that the release record itself is missing.
  const numbers = [...new Set([...records.keys(), ...deployments.map((d) => d.version)])].sort((a, b) => b - a);

  if (numbers.length === 0) {
    return <p className={ui.muted}>No releases yet. Versions appear here once a website has been generated.</p>;
  }
  const live = site?.live_version ?? null;

  return (
    <ol className={styles.releases}>
      {numbers.map((n) => {
        const v = records.get(n);
        const attempts = deployments.filter((d) => d.version === n).sort((a, b) => b.attempt - a.attempt);
        const role: ReleaseRole = v ? releaseRole(v.status, n, live) : n === live ? "current" : "other";
        const roleLabel = ROLE_LABEL[role];
        return (
          <li key={n} className={styles.release}>
            <div className={styles.releaseHead}>
              <h3 className={styles.releaseTitle}>Version {n}</h3>
              {roleLabel && <span className={`${styles.pill} ${ROLE_CLASS[role]}`}>{roleLabel}</span>}
              {v && <span className={styles.pill}>{versionStatusLabel(v.status)}</span>}
            </div>
            {v ? (
              <p className={ui.small + " " + ui.muted}>
                {v.created_at && <>Created {formatTime(v.created_at)}</>}
                {v.deployed_at && <> · Deployed {formatTime(v.deployed_at)}</>}
                {v.parent_version !== null && <> · Based on version {v.parent_version}</>}
                {v.generation_attempt > 1 && <> · Generation attempt {v.generation_attempt}</>}
              </p>
            ) : (
              <p className={ui.small + " " + ui.muted}>The release record for this version was not returned. Only its deployment attempts are shown.</p>
            )}
            {v?.error && <p className={ui.error}>{v.error}</p>}
            {attempts.length > 0 && (
              <ul className={styles.attempts} aria-label={`Deployment attempts for version ${n}`}>
                {attempts.map((d) => (
                  <li key={d.id}>
                    <span>Attempt {d.attempt}: {attemptStatusLabel(d.status)}</span>
                    <VerificationBadge level={d.verification_level} />
                    <span className={ui.small + " " + ui.muted}>
                      {d.publicly_reachable ? "Publicly reachable" : "Not confirmed publicly reachable"}
                      {" · "}Started {formatTime(d.started_at)}
                      {d.finished_at && <> · Finished {formatTime(d.finished_at)}</>}
                    </span>
                    {d.error && <span className={styles.attemptError}>{d.error}</span>}
                  </li>
                ))}
              </ul>
            )}
          </li>
        );
      })}
    </ol>
  );
}
