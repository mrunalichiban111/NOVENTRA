import type { Business } from "@/lib/api";
import type { Deployment, Site } from "@/lib/sites-api";
import ui from "../ui.module.css";
import { attemptStatusLabel, versionStatusLabel } from "./format";
import styles from "./website.module.css";

// Shown while the business is DEPLOYING. The API gives no stage detail or percentage, so this
// reports only what it does give: whether a worker holds the job, and the literal status of the
// newest release and the newest deployment attempt. It never says the site is live: that is only
// true once the business leaves DEPLOYING, and even then verification decides how far.
export default function DeployProgress({
  business,
  site,
  deployments,
}: {
  business: Business;
  site: Site | null;
  deployments: Deployment[];
}) {
  const latestAttempt = [...deployments].sort((a, b) => b.started_at.localeCompare(a.started_at))[0];
  const latestVersion = [...(site?.versions ?? [])].sort((a, b) => b.version - a.version)[0];
  const hasRecord = Boolean(latestVersion || latestAttempt);

  return (
    <div className={styles.progress} role="status" aria-live="polite" aria-busy="true">
      <span className={styles.spinner} aria-hidden="true" />
      <div>
        <p className={styles.progressTitle}>Deployment in progress</p>
        <p className={ui.muted}>
          {business.pipeline.in_progress
            ? "A worker is running your deployment."
            : "Your request was accepted and is queued for a worker."}{" "}
          Accepted means queued, not deployed: nothing is live until the deployment finishes and is verified.
          This page updates automatically, and no completion percentage is available.
        </p>
        {business.domain && <p className={ui.small}>Target address: <strong>{business.domain}</strong></p>}
        <ul className={styles.serverRecord} aria-label="What the server has recorded so far">
          {hasRecord ? (
            <>
              {latestVersion && (
                <li>Release version {latestVersion.version}: {versionStatusLabel(latestVersion.status)}</li>
              )}
              {latestAttempt && (
                <li>Deployment attempt {latestAttempt.attempt} (version {latestAttempt.version}): {attemptStatusLabel(latestAttempt.status)}</li>
              )}
            </>
          ) : (
            <li>Nothing has been recorded yet.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
