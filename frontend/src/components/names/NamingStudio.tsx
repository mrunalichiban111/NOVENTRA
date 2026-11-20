"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  generateNames,
  getNames,
  selectName,
  type NameCandidate,
  type NamesList,
} from "@/lib/names-api";
import { namingMode } from "@/lib/stages";
import ui from "../ui.module.css";
import { useWorkspace } from "../workspace/WorkspaceProvider";
import CandidateCard from "./CandidateCard";
import styles from "./names.module.css";

// Errors that mean our view of the business is stale: reload instead of just showing a message.
const STALE_CODES = new Set(["already_selected", "slug_claimed", "not_eligible", "generation_limit"]);

const errMsg = (e: unknown, fallback: string) => (e instanceof Error ? e.message : fallback);

export default function NamingStudio() {
  const { id, business, refresh } = useWorkspace();
  const mode = business ? namingMode(business) : "blocked";

  const [list, setList] = useState<NamesList | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadTick, setReloadTick] = useState(0);
  const [generating, setGenerating] = useState(false);
  const [genError, setGenError] = useState<{ message: string; transient: boolean } | null>(null);
  const [claimingId, setClaimingId] = useState<string | null>(null);
  const [claimError, setClaimError] = useState<{ id: string; message: string } | null>(null);
  // After a claim the clicked button disappears; focus the claimed-address panel so keyboard and
  // screen-reader users land on the result instead of the top of the page.
  const claimedRef = useRef<HTMLElement>(null);
  const [focusClaimed, setFocusClaimed] = useState(0);
  // One key per generation attempt; kept after a failure that may not have reached the server.
  const keyRef = useRef<string | null>(null);

  useEffect(() => {
    if (mode === "blocked") return;
    let cancelled = false;
    getNames(id).then(
      (l) => { if (!cancelled) { setList(l); setLoadError(null); } },
      (e) => { if (!cancelled) setLoadError(errMsg(e, "Could not load names.")); },
    );
    return () => { cancelled = true; };
  }, [id, mode, reloadTick]);

  useEffect(() => {
    if (focusClaimed > 0) claimedRef.current?.focus();
  }, [focusClaimed]);

  const reload = useCallback(() => { setLoadError(null); setReloadTick((t) => t + 1); }, []);

  async function generate() {
    setGenerating(true);
    setGenError(null);
    keyRef.current ??= crypto.randomUUID();
    try {
      setList(await generateNames(id, keyRef.current));
      keyRef.current = null;
    } catch (e) {
      if (e instanceof ApiError && (e.status === 409 || e.status === 422)) keyRef.current = null;
      // 409/422 mean retrying the same request cannot help; anything else may be transient.
      const transient = !(e instanceof ApiError && (e.status === 409 || e.status === 422));
      setGenError({ message: errMsg(e, "Name generation failed."), transient });
      if (e instanceof ApiError && e.code && STALE_CODES.has(e.code)) { reload(); void refresh(); }
    } finally {
      setGenerating(false);
    }
  }

  async function claim(c: NameCandidate, replace: boolean) {
    setClaimingId(c.id);
    setClaimError(null);
    try {
      await selectName(id, c.id, replace);
      await Promise.all([getNames(id).then(setList), refresh()]);
      setFocusClaimed((n) => n + 1);
    } catch (e) {
      setClaimError({ id: c.id, message: errMsg(e, "Could not claim this name.") });
      if (e instanceof ApiError && e.code && STALE_CODES.has(e.code)) { reload(); void refresh(); }
    } finally {
      setClaimingId(null);
    }
  }

  if (!business) return null;

  if (mode === "blocked") {
    const why =
      business.status === "REJECTED" ? "This idea was refused for safety reasons, so naming is not available."
      : business.verdict === "rethink" ? "A Rethink verdict does not unlock naming. Revisit your assumptions on the assessment page."
      : "Naming opens once your assessment has a Go or Go with changes verdict.";
    return (
      <div>
        <h1 className={ui.h1} style={{ fontSize: 22 }}>Naming Studio</h1>
        <p className={ui.muted} role="status">{why}</p>
        <div className={ui.actions}>
          <Link href={`/b/${id}`} className={`${ui.button} ${ui.ghost}`}>Back to assessment</Link>
        </div>
      </div>
    );
  }

  const editable = mode === "edit";
  const canGenerate = editable && business.status === "GATED";
  const used = list?.generations_used ?? 0;
  const max = list?.generations_max ?? 3;
  const limitReached = list !== null && used >= max;
  const valid = list?.candidates.filter((c) => c.valid) ?? [];
  const invalid = list?.candidates.filter((c) => !c.valid) ?? [];
  const current = valid.find((c) => c.selected);

  return (
    <div>
      <h1 className={ui.h1} style={{ fontSize: 22 }}>Naming Studio</h1>
      <p className={ui.muted} style={{ marginBottom: 20 }}>
        Generate name ideas for your business, compare them, check the web addresses and claim one
        NOVENTRA subdomain. Checking a domain never buys it, and claiming a subdomain is not buying a domain.
      </p>

      {!list && !loadError && <p className={ui.muted}>Loading names…</p>}
      {loadError && (
        <div className={ui.error} role="alert">
          {loadError}{" "}
          <button className={`${ui.button} ${ui.ghost}`} onClick={reload}>Try again</button>
        </div>
      )}

      {list && !list.subdomains_configured && (
        <p className={ui.disclaimer} role="status">
          Subdomain claiming is not configured on this server yet. You can generate and review
          names, but you cannot claim one until it is.
        </p>
      )}

      {list && (
        <section className={styles.generate} aria-label="Generate names">
          <div>
            <p className={styles.counter}>Generated {used} of {max} times</p>
            <p className={ui.small + " " + ui.muted}>
              {!canGenerate
                ? "Names can only be generated before a name is claimed."
                : limitReached
                  ? "You have used all name generations."
                  : used > 0
                    ? "Generating again replaces the names you have not claimed."
                    : "Names are drafted from your assessment, then ranked by a simple heuristic."}
            </p>
          </div>
          {canGenerate && (
            <button className={ui.button} onClick={generate} disabled={generating || limitReached}>
              {generating ? "Generating…" : used > 0 ? "Generate new names" : "Generate names"}
            </button>
          )}
        </section>
      )}
      {genError && (
        <div className={ui.error} role="alert">
          {genError.message}
          {genError.transient && canGenerate && <> You can try again.</>}
        </div>
      )}

      {current && (
        <section ref={claimedRef} tabIndex={-1} className={styles.claimedPanel} aria-label="Claimed subdomain">
          <p className={styles.claimedLabel}>Your NOVENTRA address</p>
          <p className={styles.claimedHost}>{current.subdomain_host}</p>
          <p className={ui.small + " " + ui.muted}>
            Reserved on NOVENTRA. No domain has been purchased
            {business.status === "DOMAIN_PENDING" ? " and no website has been created yet." : "."}
          </p>
          <div className={ui.actions}>
            <Link href={`/b/${id}/website`} className={ui.button}>Continue to Website Launch</Link>
          </div>
        </section>
      )}

      {list && list.candidates.length === 0 && !generating && (
        <p className={styles.empty}>
          {canGenerate ? "No names yet. Generate a first set to get started." : "No names were generated for this business."}
        </p>
      )}

      {valid.length > 0 && (
        <>
          <h2 className={ui.h2} style={{ marginTop: 28 }}>Ranked names</h2>
          <div className={styles.candidates}>
            {valid.map((c) => (
              <CandidateCard
                key={c.id}
                businessId={id}
                candidate={c}
                editable={editable}
                claimConfigured={list?.subdomains_configured ?? false}
                current={current}
                claiming={claimingId === c.id}
                claimLocked={claimingId !== null}
                claimError={claimError?.id === c.id ? claimError.message : null}
                onClaim={claim}
              />
            ))}
          </div>
        </>
      )}

      {invalid.length > 0 && (
        <details className={styles.invalid}>
          <summary>{invalid.length} name{invalid.length === 1 ? "" : "s"} we could not use</summary>
          <ul className={ui.list}>
            {invalid.map((c) => (
              <li key={c.id}><strong>{c.name}</strong>: {(c.invalid_reason ?? "invalid").replace(/_/g, " ")}</li>
            ))}
          </ul>
        </details>
      )}

      {list && <p className={ui.disclaimer}>{list.disclaimer}</p>}
    </div>
  );
}
