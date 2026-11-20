"use client";

// Improvement Studio (M6: FR-IM-1..3, FR-IM-6, FR-UI-4). The backend decides classification,
// validation, the D >= D_original + 3 rule and promotion; this page only shows what the API
// returns and sends explicit founder clicks. Every D shown is SIMULATED.

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  answerQuestion, approveItems, discardDraft, generateProposal, getAttempt, promoteDraft,
  rejectProposal, rollbackSite, startAttempt, type Attempt, type Classification, type EvalSummary,
} from "@/lib/improve-api";
import styles from "../personas/personas.module.css";
import ui from "../ui.module.css";
import { useWorkspace } from "../workspace/WorkspaceProvider";

const POLL_MS = 2500;
const CLASS_LABEL: Record<Classification, string> = {
  SAFE_AUTOMATIC: "Wording only",
  FOUNDER_INPUT_REQUIRED: "Needs your facts",
  APPROVAL_REQUIRED: "Needs your approval",
};
const OUTCOME: Record<string, string> = {
  promoted: "Promoted: the improved version is now live.",
  not_promoted: "Not promoted: your original page stays live.",
  discarded: "Draft discarded: your original page stays live.",
  rejected: "Proposal rejected: nothing changed.",
  stale: "Out of date: your live page changed, so this proposal was stopped.",
};
const REASON: Record<string, string> = {
  draft_not_improved: "The draft did not beat the original by at least 3 simulated points.",
  evaluation_incomplete: "One of the simulated evaluations was incomplete, so the scores are not comparable.",
  proposal_stale: "The live page changed after this proposal was made.",
  approval_not_granted: "A price, plan or guarantee change was not approved.",
  deploy_failed: "Activation or verification failed; the original release was kept.",
};

function msg(e: unknown, fallback: string) {
  return e instanceof ApiError || e instanceof Error ? e.message || fallback : fallback;
}

function Score({ label, e }: { label: string; e: EvalSummary | null }) {
  return (
    <div className={styles.score}>
      <div className={styles.scoreLabel}>{label}{e?.site_version ? ` (v${e.site_version})` : ""}</div>
      <div className={styles.scoreValue}>{e?.d_score != null ? e.d_score.toFixed(1) : "—"}</div>
      <div className={styles.scoreSub}>
        {e?.d_score != null ? `± ${e.d_se?.toFixed(1)} SE` : e?.status === "complete" ? "" : `${e?.completed_runs ?? 0} of 90 done`}
      </div>
    </div>
  );
}

export default function ImproveStudio() {
  const { id } = useWorkspace();
  const [a, setA] = useState<Attempt | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [chosen, setChosen] = useState<Record<string, boolean>>({});
  const [consequentialOk, setConsequentialOk] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getAttempt(id).then(
      (r) => { if (!cancelled) { setA(r); setError(null); } },
      (e) => { if (!cancelled) setError(msg(e, "Could not load the improvement studio.")); },
    );
    return () => { cancelled = true; };
  }, [id, tick]);

  const working = !!a && (["approved_for_draft", "draft_built"].includes(a.status) || a.promote_requested);
  useEffect(() => {
    if (!working) return;
    const t = setTimeout(() => setTick((n) => n + 1), POLL_MS);
    return () => clearTimeout(t);
  }, [working, tick]);

  async function act(fn: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(msg(e, fallback));
    } finally {
      setBusy(false);
      setTick((n) => n + 1);
    }
  }

  const items = a?.proposal.items ?? [];
  const isChosen = (iid: string, cls: Classification) => chosen[iid] ?? cls !== "APPROVAL_REQUIRED";
  const selected = items.filter((i) => isChosen(i.id, i.classification));
  const needsApproval = selected.some((i) => i.classification === "APPROVAL_REQUIRED");
  const unanswered = (a?.questions ?? []).filter((q) => !a?.answers[q.id]);

  return (
    <div>
      <h1 className={ui.h1} style={{ fontSize: 22 }}>Improvement Studio</h1>
      <p className={styles.banner} role="note">
        <span className={styles.bannerTitle}>Simulated results</span>
        <span>Changes are prepared as a draft and tested on the same simulated panel. Nothing goes live without your click and a gain of at least 3 points.</span>
      </p>
      {error && <div className={ui.error} role="alert">{error}</div>}
      {!a && !error && <p className={ui.muted} role="status">Loading…</p>}

      {a && (a.status === "none" || OUTCOME[a.status]) && (
        <section className={styles.section} aria-labelledby="start-h">
          <h2 id="start-h" className={ui.h2}>{a.status === "none" ? "Start" : "Result"}</h2>
          {OUTCOME[a.status] && (
            <p role="status" style={{ fontWeight: 600 }}>
              {OUTCOME[a.status]} {a.reason && REASON[a.reason] ? REASON[a.reason] : ""}
            </p>
          )}
          {a.status === "promoted" && a.source_version && (
            <p className={ui.muted} style={{ marginTop: 8 }}>
              Changed your mind? <button className={`${ui.button} ${ui.ghost}`} disabled={busy}
                onClick={() => act(() => rollbackSite(id, a.source_version!), "Rollback failed.")}>Roll back to v{a.source_version}</button>
            </p>
          )}
          <p className={ui.muted} style={{ marginTop: 8 }}>Uses the latest complete simulated evaluation of your live page. <Link href={`/b/${id}/personas`}>Persona Arena</Link></p>
          <div className={ui.actions}>
            <button className={ui.button} disabled={busy} onClick={() => act(() => startAttempt(id), "Could not start.")}>
              {a.status === "none" ? "Find improvements" : "Start a new improvement"}
            </button>
          </div>
        </section>
      )}

      {a && a.opportunities.length > 0 && !OUTCOME[a.status] && (
        <section className={styles.section} aria-labelledby="opp-h">
          <h2 id="opp-h" className={ui.h2}>1. Opportunities (from {a.original?.completed_runs ?? 90} simulated evaluations)</h2>
          <ol className={styles.objections}>
            {a.opportunities.map((o) => (
              <li key={o.id} className={styles.objection}>
                <span className={styles.objCount} aria-label={`${o.evidence_count} mentions`}>{o.evidence_count}</span>
                <span className={styles.objTitle}>{o.objection_type.replace("_", " ")} · {o.section.replace(/_/g, " ")} · <em>{CLASS_LABEL[o.classification]}</em></span>
                <span className={styles.objExample}>“{o.example || "—"}”</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {a?.status === "awaiting_answers" && (
        <section className={styles.section} aria-labelledby="q-h">
          <h2 id="q-h" className={ui.h2}>2. A few facts only you know</h2>
          {a.questions.length === 0 && <p className={ui.muted}>No facts needed for these changes.</p>}
          <div className={ui.form}>
            {a.questions.map((q) => {
              const saved = a.answers[q.id];
              return (
                <div key={q.id}>
                  <label className={ui.label} htmlFor={`ans-${q.id}`}>{q.question}</label>
                  <textarea id={`ans-${q.id}`} className={ui.textarea} style={{ minHeight: 70 }} maxLength={300}
                    value={drafts[q.id] ?? saved?.value ?? ""} onChange={(e) => setDrafts({ ...drafts, [q.id]: e.target.value })} />
                  <div className={ui.actions}>
                    <button className={ui.button} disabled={busy || !(drafts[q.id] ?? "").trim()}
                      onClick={() => act(() => answerQuestion(id, q.id, drafts[q.id], false), "Could not save.")}>Save answer</button>
                    <button className={`${ui.button} ${ui.ghost}`} disabled={busy}
                      onClick={() => act(() => answerQuestion(id, q.id, null, true), "Could not save.")}>Not decided yet</button>
                    {saved && <span className={ui.small} role="status">Saved: {saved.status === "answered" ? "answered" : "not decided (section left unchanged)"}</span>}
                  </div>
                </div>
              );
            })}
          </div>
          <div className={ui.actions}>
            <button className={ui.button} disabled={busy || unanswered.length > 0}
              onClick={() => act(() => generateProposal(id), "Could not prepare changes.")}>
              {busy ? "Preparing…" : "Prepare proposed changes"}
            </button>
            {unanswered.length > 0 && <span className={ui.small}>{unanswered.length} question(s) left</span>}
          </div>
        </section>
      )}

      {a?.status === "proposed" && (
        <section className={styles.section} aria-labelledby="p-h">
          <h2 id="p-h" className={ui.h2}>3. Proposed changes</h2>
          <ul className={styles.objections}>
            {items.map((i) => (
              <li key={i.id} className={styles.card}>
                <label style={{ display: "flex", gap: 8, alignItems: "baseline", fontWeight: 600 }}>
                  <input type="checkbox" checked={isChosen(i.id, i.classification)}
                    onChange={(e) => setChosen({ ...chosen, [i.id]: e.target.checked })} />
                  {i.path} · {CLASS_LABEL[i.classification]}
                </label>
                <span className={styles.cardMeta}>Before</span>
                <del className={styles.cardReason}>{i.old}</del>
                <span className={styles.cardMeta}>After</span>
                <ins className={styles.cardReason} style={{ textDecoration: "none", fontWeight: 600 }}>{i.new}</ins>
                <span className={styles.cardMeta}>{i.rationale}</span>
              </li>
            ))}
          </ul>
          {needsApproval && (
            <label style={{ display: "flex", gap: 8, marginTop: 12 }}>
              <input type="checkbox" checked={consequentialOk} onChange={(e) => setConsequentialOk(e.target.checked)} />
              I approve the price, plan or guarantee changes selected above.
            </label>
          )}
          <div className={ui.actions}>
            <button className={ui.button} disabled={busy || selected.length === 0 || (needsApproval && !consequentialOk)}
              onClick={() => act(() => approveItems(id, selected.map((i) => i.id), consequentialOk), "Could not approve.")}>
              Build and test a draft ({selected.length})
            </button>
            <button className={`${ui.button} ${ui.ghost}`} disabled={busy} onClick={() => act(() => rejectProposal(id), "Could not reject.")}>Reject all</button>
          </div>
        </section>
      )}

      {a && ["approved_for_draft", "draft_built", "draft_evaluated"].includes(a.status) && (
        <section className={styles.section} aria-labelledby="d-h">
          <h2 id="d-h" className={ui.h2}>4. Draft{a.draft_version ? ` v${a.draft_version}` : ""} vs your live page</h2>
          {a.status === "approved_for_draft" && <p role="status"><span className={styles.spinner} aria-hidden="true" />Building the draft (your live page is not touched)…</p>}
          {a.status === "draft_built" && <p role="status" aria-live="polite"><span className={styles.spinner} aria-hidden="true" />Testing the draft on the same simulated panel… {a.draft?.completed_runs ?? 0} of 90</p>}
          <div className={styles.scoreRow}>
            <Score label="Live D" e={a.original} />
            <Score label="Draft D" e={a.draft} />
            {a.delta_demand != null && (
              <div className={styles.score}>
                <div className={styles.scoreLabel}>Change</div>
                <div className={styles.scoreValue}>{a.delta_demand > 0 ? "+" : ""}{a.delta_demand.toFixed(1)}</div>
                <div className={styles.scoreSub}>needs ≥ +{a.threshold}</div>
              </div>
            )}
          </div>
          <p className={styles.note}>{a.note}</p>
          {a.status === "draft_evaluated" && (
            <div className={ui.actions}>
              <button className={ui.button} disabled={busy || a.promote_requested} onClick={() => act(() => promoteDraft(id), "Promotion refused.")}>
                {a.promote_requested ? "Promoting…" : "Promote draft to live"}
              </button>
              <button className={`${ui.button} ${ui.ghost}`} disabled={busy || a.promote_requested} onClick={() => act(() => discardDraft(id), "Could not discard.")}>Discard draft</button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
