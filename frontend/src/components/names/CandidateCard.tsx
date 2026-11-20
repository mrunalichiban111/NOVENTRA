"use client";

import { useEffect, useRef, useState } from "react";
import {
  AVAILABILITY_TOP_N,
  checkAvailability,
  type DomainCheck,
  type NameCandidate,
} from "@/lib/names-api";
import ui from "../ui.module.css";
import DomainStatusBadge from "./DomainStatusBadge";
import styles from "./names.module.css";

const FACTOR_LABEL: Record<string, string> = {
  length: "Length",
  pronounceability: "Pronounceability",
  no_digits: "No digits",
  no_hyphens: "No hyphens",
  typeability: "Easy to type",
  keyword_overlap: "Matches your idea",
};

interface Props {
  businessId: string;
  candidate: NameCandidate;
  editable: boolean; // generate/check/claim allowed (GATED or DOMAIN_PENDING)
  claimConfigured: boolean; // BASE_DOMAIN set on the server
  current: NameCandidate | undefined; // the candidate already claimed, if any
  claiming: boolean; // this card's claim request is in flight
  claimLocked: boolean; // some claim request is in flight
  claimError: string | null;
  onClaim: (candidate: NameCandidate, replace: boolean) => void;
}

export default function CandidateCard(p: Props) {
  const c = p.candidate;
  const [checks, setChecks] = useState<DomainCheck[] | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  // Inline confirmation: move focus into it when it opens, and back to the trigger when it is
  // cancelled, so keyboard users are never dropped on <body>. Escape cancels.
  const triggerRef = useRef<HTMLButtonElement>(null);
  const confirmRef = useRef<HTMLButtonElement>(null);
  const restoreFocus = useRef(false);
  useEffect(() => {
    if (confirming) confirmRef.current?.focus();
    else if (restoreFocus.current) { restoreFocus.current = false; triggerRef.current?.focus(); }
  }, [confirming]);
  const cancelConfirm = () => { restoreFocus.current = true; setConfirming(false); };

  const shown = checks ?? c.domain_checks;
  const checkable = c.valid && c.rank !== null && c.rank <= AVAILABILITY_TOP_N;

  async function runCheck() {
    setChecking(true);
    setCheckError(null);
    try {
      setChecks((await checkAvailability(p.businessId, c.id)).results);
    } catch (e) {
      setCheckError(e instanceof Error ? e.message : "The domain check failed.");
    } finally {
      setChecking(false);
    }
  }

  const switching = p.current !== undefined && !c.selected;
  const score = c.score_total === null ? null : Number(c.score_total);
  const factors = Object.entries(c.score_factors ?? {});

  return (
    <article className={`${styles.candidate} ${c.selected ? styles.candidateSelected : ""}`}>
      <header className={styles.candHead}>
        {c.rank !== null && <span className={styles.rank} aria-label={`Rank ${c.rank}`}>#{c.rank}</span>}
        <div className={styles.candTitle}>
          <h3 className={styles.candName}>{c.name}</h3>
          {c.subdomain_host && <p className={styles.host}>{c.subdomain_host}</p>}
        </div>
        {c.selected && <span className={styles.claimedTag}>Claimed</span>}
      </header>

      {c.rationale && <p className={styles.rationale}>{c.rationale}</p>}

      {score !== null && (
        <details className={styles.score}>
          <summary>Score {score.toFixed(1)} / 100 <span className={ui.muted}>(web-address heuristic)</span></summary>
          <ul className={styles.factors}>
            {factors.map(([key, f]) => (
              <li key={key}>
                <span>{FACTOR_LABEL[key] ?? key}</span>
                <span>{f.points.toFixed(1)} / {f.weight}</span>
              </li>
            ))}
          </ul>
        </details>
      )}

      {checkable && (
        <section className={styles.domains} aria-label={`Domain availability for ${c.name}`}>
          {shown.length > 0 && (
            <ul className={styles.domainList}>
              {shown.map((d) => (
                <li key={d.tld} className={styles.domainRow}>
                  <span className={styles.domainName}>{d.domain}</span>
                  <DomainStatusBadge status={d.status} />
                  <span className={styles.domainMsg}>
                    {d.message}
                    {d.status !== "UNKNOWN" && d.definitive === false && " (not definitive)"}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {shown.length > 0 && shown[0].checked_at && (
            <p className={ui.small + " " + ui.muted}>
              Last checked {new Date(shown[0].checked_at).toLocaleString()}. Checking never buys or registers a domain.
            </p>
          )}
          {checkError && <p className={ui.error} role="alert">{checkError}</p>}
          {p.editable && (
            <button className={`${ui.button} ${ui.ghost}`} onClick={runCheck} disabled={checking}>
              {checking ? "Checking…" : shown.length > 0 ? "Check again" : "Check domains"}
            </button>
          )}
        </section>
      )}

      {c.valid && !checkable && p.editable && (
        <p className={ui.small + " " + ui.muted}>Domain checks cover the top {AVAILABILITY_TOP_N} names only.</p>
      )}

      {c.valid && p.editable && !c.selected && (
        <footer className={styles.claim}>
          {!c.subdomain_available ? (
            <p className={ui.muted}>This subdomain is already claimed by another business.</p>
          ) : !p.claimConfigured ? (
            <p className={ui.muted}>Claiming is not available until the server has a subdomain base configured.</p>
          ) : confirming ? (
            <div className={ui.actions} role="group" aria-label="Confirm switching names" onKeyDown={(e) => { if (e.key === "Escape") cancelConfirm(); }}>
              <span className={ui.small}>Replace <strong>{p.current?.subdomain_host}</strong> with <strong>{c.subdomain_host}</strong>?</span>
              <button
                ref={confirmRef}
                className={ui.button}
                disabled={p.claimLocked}
                onClick={() => { setConfirming(false); p.onClaim(c, true); }}
              >
                Confirm switch
              </button>
              <button className={`${ui.button} ${ui.ghost}`} onClick={cancelConfirm}>Cancel</button>
            </div>
          ) : (
            <div className={ui.actions}>
              <button
                ref={triggerRef}
                className={ui.button}
                disabled={p.claimLocked}
                onClick={() => (switching ? setConfirming(true) : p.onClaim(c, false))}
              >
                {p.claiming ? "Claiming…" : switching ? "Switch to this name" : "Claim this subdomain"}
              </button>
              <span className={ui.small + " " + ui.muted}>Reserves {c.subdomain_host} on NOVENTRA. It does not buy a domain.</span>
            </div>
          )}
          {p.claimError && <p className={ui.error} role="alert">{p.claimError}</p>}
        </footer>
      )}
    </article>
  );
}
