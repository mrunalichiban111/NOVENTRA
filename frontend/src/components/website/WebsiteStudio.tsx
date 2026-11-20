"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { ApiError, retryBusiness } from "@/lib/api";
import { getNames } from "@/lib/names-api";
import { getDeployments, getSite, requestDeploy, type Deployment, type Site } from "@/lib/sites-api";
import { websiteMode } from "@/lib/stages";
import ui from "../ui.module.css";
import { useWorkspace } from "../workspace/WorkspaceProvider";
import DeployProgress from "./DeployProgress";
import ReleaseHistory from "./ReleaseHistory";
import SitePreview from "./SitePreview";
import VerificationBadge, { levelContradictsReachability, reachabilityText, verificationText } from "./VerificationBadge";
import styles from "./website.module.css";

const STATUS_LABEL: Record<string, string> = {
  DOMAIN_PENDING: "Name claimed, ready to deploy",
  DEPLOYING: "Deploying",
  // "Live" here is NOVENTRA's pipeline status, not proof of a public website: the Verification
  // section says how far the release was actually verified.
  LIVE: "Marked live (see Verification)",
  TESTING: "Marked live, testing",
  IMPROVING: "Marked live, improving",
  DONE: "Done",
  FAILED: "Failed",
};

type Notice = { tone: "info" | "error"; title: string; text: string; namesLink?: boolean };
type Availability = "unconfirmed" | "not_enabled" | "accepted";

// Turns a failed deploy request into honest copy. Nothing here retries: the founder must press
// the button again.
function describeDeployError(e: unknown): { notice: Notice; notEnabled: boolean } {
  const message = e instanceof Error ? e.message : "The request failed.";
  const code = e instanceof ApiError ? e.code : null;
  const status = e instanceof ApiError ? e.status : null;
  const err = (title: string, text: string, namesLink = false): Notice => ({ tone: "error", title, text, namesLink });
  if (code === "deploy_not_configured") {
    return {
      notEnabled: true,
      notice: err(
        "Deployment is not enabled in this environment",
        "The server refused this request because no deployment provider is configured. Nothing was generated, published or verified, and your claimed address is unchanged.",
      ),
    };
  }
  if (code === "deploy_misconfigured") {
    return { notEnabled: false, notice: err("Deployment is misconfigured", "The server's deployment settings are unsafe or invalid, so nothing was started. The operator needs to check the server logs.") };
  }
  if (code === "no_claim") return { notEnabled: false, notice: err("No name is claimed yet", message, true) };
  if (code === "not_eligible") {
    return { notEnabled: false, notice: err("This business can't be deployed right now", `${message} Deployment needs a claimed name and a business that is waiting to deploy.`) };
  }
  if (status === 404) return { notEnabled: false, notice: err("Business not found", "The server has no business with this address.") };
  if (status === 429) return { notEnabled: false, notice: err("Too many requests", message) };
  return {
    notEnabled: false,
    notice: err("The request did not go through", `${message} Nothing is retried automatically. Check your connection and press the button to try again.`),
  };
}

// A FAILED business whose failure was not at DEPLOYING is retried from the stage that failed,
// not from here. The retry endpoint is shared by all stages, so the Website Studio only
// offers it for DEPLOYING failures and points everything else to the right workspace.
function failedElsewhere(stage: string | null): string {
  if (stage === "INTAKE" || stage === "PREMORTEM") {
    return "Your assessment failed before a website could be planned. Retry it from the Assessment page.";
  }
  if (stage === "DOMAIN_PENDING") {
    return "Your launch failed while a name was being claimed. Review it in the Naming Studio.";
  }
  return "This business failed outside website deployment. Review it on the Assessment page.";
}

// One honest sentence for a business NOVENTRA has marked live, driven by what verification found.
function liveSentence(site: Site | null, loaded: boolean): string {
  if (!loaded) return "This business is marked live. Loading the release details…";
  if (!site || site.live_version === null) {
    return "This business is marked live, but the server reports no deployed release. The data may be out of date.";
  }
  const v = `Version ${site.live_version}`;
  const level = site.verification_level;
  if (levelContradictsReachability(level, site.publicly_reachable)) {
    return `${v} is marked live, but the server's verification data is inconsistent. It is not treated as a public site.`;
  }
  if (level === "public") return `${v} is live and verified as publicly reachable.`;
  if (level === "local") return `${v} is marked live after local verification only. It has not been confirmed reachable from the internet.`;
  if (level === "simulated") return `${v} is marked live after a simulated deployment. Nothing real was published.`;
  return `${v} is marked live, but it has no recorded verification. It is not confirmed publicly reachable.`;
}

export default function WebsiteStudio() {
  const { id, business, tick, refresh, update } = useWorkspace();
  const mode = business ? websiteMode(business) : "blocked";
  const status = business?.status ?? null;

  const [site, setSite] = useState<Site | null>(null);
  const [deployments, setDeployments] = useState<Deployment[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [claimedName, setClaimedName] = useState<string | null>(null);
  const [busy, setBusy] = useState<"deploy" | "retry" | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [availability, setAvailability] = useState<Availability>("unconfirmed");
  // The deploy/retry button disappears once the business moves to DEPLOYING; park focus on the
  // Launch heading so keyboard and screen-reader users stay oriented.
  const launchRef = useRef<HTMLHeadingElement>(null);

  // Site and deployment data follow the workspace's single poll: while DEPLOYING they refetch
  // whenever the provider's tick changes; otherwise only when the status changes. No timers here.
  const followTick = status === "DEPLOYING" ? tick : 0;
  useEffect(() => {
    if (mode !== "ready") return;
    let cancelled = false;
    Promise.all([getSite(id), getDeployments(id)]).then(
      ([s, d]) => {
        if (cancelled) return;
        setSite(s);
        setDeployments(d.deployments);
        setLoadError(null);
        setLoaded(true);
      },
      (e) => { if (!cancelled) setLoadError(e instanceof Error ? e.message : "Could not load the site."); },
    );
    return () => { cancelled = true; };
  }, [id, mode, status, followTick, reloadTick]);

  // The business has no name field; the claimed name is the selected naming candidate.
  useEffect(() => {
    if (mode !== "ready") return;
    let cancelled = false;
    getNames(id).then(
      (l) => { if (!cancelled) setClaimedName(l.candidates.find((c) => c.selected)?.name ?? null); },
      () => { /* the host alone is enough to identify the site */ },
    );
    return () => { cancelled = true; };
  }, [id, mode]);

  async function deploy() {
    setBusy("deploy");
    setNotice(null);
    try {
      const out = await requestDeploy(id);
      setAvailability("accepted");
      setNotice(
        out.changed
          ? { tone: "info", title: "Deployment request accepted. Not deployed yet.", text: "Accepted means queued for a worker. Your site is not live until generation, publishing and verification finish, and this page will show the result." }
          : { tone: "info", title: "Already in progress or live", text: "Nothing new was started; this business is already deploying or live." },
      );
      await refresh();
      launchRef.current?.focus();
    } catch (e) {
      const d = describeDeployError(e);
      if (d.notEnabled) setAvailability("not_enabled");
      setNotice(d.notice);
      if (e instanceof ApiError && e.code === "not_eligible") void refresh();
    } finally {
      setBusy(null);
    }
  }

  async function retry() {
    setBusy("retry");
    setNotice(null);
    try {
      update(await retryBusiness(id));
      launchRef.current?.focus();
    } catch (e) {
      setNotice({ tone: "error", title: "Retry was not accepted", text: e instanceof Error ? e.message : "Retry failed." });
    } finally {
      setBusy(null);
    }
  }

  if (!business) return null;

  // Past the naming stage but the API returned no claimed address: an incomplete response, not a
  // normal state. Say so instead of showing the generic "claim a name" text.
  const claimExpected =
    ["DOMAIN_PENDING", "DEPLOYING", "LIVE", "TESTING", "IMPROVING", "DONE"].includes(business.status) && !business.domain;

  if (mode !== "ready") {
    return (
      <div>
        <h1 className={ui.h1} style={{ fontSize: 22 }}>Website Launch</h1>
        {mode === "no_claim" ? (
          <>
            <p className={ui.muted} role="status">You have not claimed a name yet. Your website is published at the NOVENTRA address you claim in the Naming Studio.</p>
            <div className={ui.actions}><Link href={`/b/${id}/names`} className={ui.button}>Go to Naming Studio</Link></div>
          </>
        ) : (
          <>
            <p className={ui.muted} role="status">
              {business.status === "FAILED" ? failedElsewhere(business.pipeline.failed_stage)
                : claimExpected ? "The server reports no claimed address for this business, so there is nothing to launch yet. Check your name in the Naming Studio."
                : business.status === "REJECTED" ? "This idea was refused for safety reasons, so no website can be created."
                : business.verdict === "rethink" ? "A Rethink verdict does not unlock a website."
                : "A website becomes available after the assessment passes and you claim a name."}
            </p>
            <div className={ui.actions}>
              {claimExpected && business.status !== "FAILED"
                ? <Link href={`/b/${id}/names`} className={ui.button}>Go to Naming Studio</Link>
                : <Link href={`/b/${id}`} className={`${ui.button} ${ui.ghost}`}>Back to assessment</Link>}
            </div>
          </>
        )}
      </div>
    );
  }

  const host = business.domain;
  const failed = status === "FAILED";
  const hasLive = site !== null && site.live_version !== null;
  // site.status is a copy of the business status taken when /site was read. If they differ the
  // site data predates the latest poll and will be refetched, so say so instead of mixing them.
  const stale = site !== null && site.status !== business.status;
  const noLiveText =
    status === "DEPLOYING" ? "Unknown until the deployment finishes."
    : failed ? "Nothing was deployed, so nothing is verified."
    : "Nothing is deployed yet.";
  const remaining = Math.max(0, business.pipeline.max_attempts - business.pipeline.attempts);
  const liveLike = status === "LIVE" || status === "TESTING" || status === "IMPROVING" || status === "DONE";
  const latestFailure = [...deployments].filter((d) => d.error).sort((a, b) => b.started_at.localeCompare(a.started_at))[0];
  const availabilityText =
    availability === "not_enabled" ? "Not enabled in this environment"
    : availability === "accepted" ? "The server accepted a request (accepted is not complete)"
    : "Unconfirmed. The server reports this only when you start a deployment";

  return (
    <div>
      <header className={styles.identity}>
        <p className={styles.eyebrow}>Your website</p>
        <h1 className={styles.siteName}>{claimedName ?? host}</h1>
        {claimedName && <p className={styles.siteHost}>{host}</p>}
        <dl className={styles.facts}>
          <div><dt>Business status</dt><dd>{STATUS_LABEL[business.status] ?? business.status}</dd></div>
          <div><dt>Deployment</dt><dd>{availabilityText}</dd></div>
          <div>
            <dt>Verification</dt>
            <dd>
              {site && site.live_version !== null ? <VerificationBadge level={site.verification_level} /> : "Not verified yet"}
            </dd>
          </div>
        </dl>
      </header>

      {loadError && (
        <div className={ui.error} role="alert">
          {loadError}{" "}
          <button className={`${ui.button} ${ui.ghost}`} onClick={() => setReloadTick((t) => t + 1)}>Try again</button>
        </div>
      )}

      <section className={styles.section} aria-labelledby="launch-h">
        <h2 id="launch-h" ref={launchRef} tabIndex={-1} className={ui.h2}>Launch</h2>

        {status === "DOMAIN_PENDING" && (
          <>
            <p className={ui.muted} style={{ maxWidth: 620 }}>
              NOVENTRA will write your site&rsquo;s content, publish it at <strong>{host}</strong> and verify it.
              No domain is purchased. Whether deployment is switched on is only known once you start it.
            </p>
            <div className={ui.actions}>
              <button className={ui.button} onClick={deploy} disabled={busy !== null}>
                {busy === "deploy" ? "Requesting…" : "Generate and deploy website"}
              </button>
            </div>
          </>
        )}

        {status === "DEPLOYING" && <DeployProgress business={business} site={site} deployments={deployments} />}

        {liveLike && (
          <p className={styles.liveLine}>{liveSentence(site, loaded)}</p>
        )}

        {failed && business.pipeline.failed_stage !== "DEPLOYING" && (
          <>
            <p className={ui.muted} role="status">{failedElsewhere(business.pipeline.failed_stage)}</p>
            <div className={ui.actions}>
              <Link
                href={business.pipeline.failed_stage === "DOMAIN_PENDING" ? `/b/${id}/names` : `/b/${id}`}
                className={`${ui.button} ${ui.ghost}`}
              >
                {business.pipeline.failed_stage === "DOMAIN_PENDING" ? "Go to Naming Studio" : "Go to assessment"}
              </Link>
            </div>
          </>
        )}

        {failed && business.pipeline.failed_stage === "DEPLOYING" && (
          <>
            <p className={ui.error}>
              {business.pipeline.last_error ?? latestFailure?.error ?? "The deployment failed."}
            </p>
            <p className={ui.muted} style={{ marginTop: 8 }}>
              The previous release, if there was one, stays in place, and your claimed name is unchanged.
            </p>
            <p className={ui.small + " " + ui.muted} style={{ marginTop: 8, maxWidth: 620 }}>
              {remaining === 0
                ? "No retries remain. "
                : `${remaining} ${remaining === 1 ? "retry remains" : "retries remain"}. `}
              This allowance is shared across all pipeline stages, so earlier failures in your assessment count
              against it too. It is not reserved for deployments.
            </p>
            {business.pipeline.retryable === true ? (
              <>
                <p className={ui.small + " " + ui.muted} style={{ marginTop: 8, maxWidth: 620 }}>
                  Retrying resumes deployment from where it stopped. If your website content needs to be
                  generated again, this may trigger another paid AI request.
                </p>
                <div className={ui.actions}>
                  <button className={ui.button} onClick={retry} disabled={busy !== null}>
                    {busy === "retry" ? "Retrying…" : "Retry website deployment"}
                  </button>
                </div>
              </>
            ) : (
              <p className={ui.muted} style={{ marginTop: 8 }}>The retry limit has been reached.</p>
            )}
          </>
        )}

        {notice && (
          <div className={notice.tone === "error" ? styles.noticeError : styles.noticeInfo} role={notice.tone === "error" ? "alert" : "status"}>
            <p className={styles.noticeTitle}>{notice.title}</p>
            <p>{notice.text}</p>
            {notice.namesLink && <p><Link href={`/b/${id}/names`} className={styles.inlineLink}>Go to Naming Studio</Link></p>}
          </div>
        )}
      </section>

      <section className={styles.section} aria-labelledby="verification-h">
        <h2 id="verification-h" className={ui.h2}>Verification</h2>
        {stale && <p className={ui.small + " " + ui.muted} role="status">Refreshing release details…</p>}
        <dl className={styles.verify}>
          <div>
            <dt>Verification level</dt>
            <dd>
              <VerificationBadge level={hasLive ? site.verification_level : null} />
              <span className={ui.muted}> {hasLive ? verificationText(site.verification_level) : noLiveText}</span>
            </dd>
          </div>
          <div>
            <dt>Publicly reachable</dt>
            <dd>{hasLive ? reachabilityText(site.verification_level, site.publicly_reachable) : noLiveText}</dd>
          </div>
          <div>
            <dt>Live version</dt>
            <dd>
              {hasLive ? `Version ${site.live_version}` : "None"}
              {hasLive && failed && <span className={ui.muted}> (the earlier release still in place; the latest deployment failed)</span>}
            </dd>
          </div>
        </dl>
      </section>

      <section className={styles.section} aria-labelledby="preview-h">
        <h2 id="preview-h" className={ui.h2}>Preview</h2>
        <SitePreview site={site} host={host} status={business.status} notEnabled={availability === "not_enabled"} />
      </section>

      <section className={styles.section} aria-labelledby="history-h">
        <h2 id="history-h" className={ui.h2}>Release history</h2>
        {!loaded && !loadError ? <p className={ui.muted}>Loading releases…</p> : <ReleaseHistory site={site} deployments={deployments} />}
      </section>
    </div>
  );
}
