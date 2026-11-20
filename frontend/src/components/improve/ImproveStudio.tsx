"use client";

// Improvement Studio (M6: FR-IM-1..3, FR-IM-6, FR-UI-4). The backend decides classification,
// validation, the D >= D_original + 3 rule and promotion; this page only shows what the API
// returns and sends explicit founder clicks. Every D shown is SIMULATED.

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import {
  answerQuestion, approveItems, discardDraft, generateProposal, getAttempt, promoteDraft,
  rejectProposal, rollbackSite, startAttempt, type Attempt, type Classification, type EvalSummary, type ProposalItem,
} from "@/lib/improve-api";
import { fieldLabel, objectionLabel, sectionLabel } from "@/lib/improve-labels";
import { EmptyState, Loading, Notice, PageHeader, SectionHead } from "../Page";
import styles from "../personas/personas.module.css";
import ui from "../ui.module.css";
import { useWorkspace } from "../workspace/WorkspaceProvider";

const POLL_MS = 2500;
const CLASS_LABEL: Record<Classification, string> = {
  SAFE_AUTOMATIC: "Wording only",
  FOUNDER_INPUT_REQUIRED: "Uses facts you provide",
  APPROVAL_REQUIRED: "Needs your approval",
};
const CLASS_TONE: Record<Classification, string> = {
  SAFE_AUTOMATIC: "",
  FOUNDER_INPUT_REQUIRED: ui.pillWarn,
  APPROVAL_REQUIRED: ui.pillBad,
};
const OUTCOME: Record<string, string> = {
  promoted: "Done: the improved version is now live.",
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

const isConsequential = (c: Classification) => c === "APPROVAL_REQUIRED";

function Score({ label, e }: { label: string; e: EvalSummary | null }) {
  return (
    <div className={styles.score}>
      <div className={styles.scoreLabel}>{label}{e?.site_version ? ` (version ${e.site_version})` : ""}</div>
      <div className={styles.scoreValue}>{e?.d_score != null ? e.d_score.toFixed(1) : "—"}</div>
      <div className={styles.scoreSub}>
        {e?.d_score != null ? `± ${e.d_se?.toFixed(1)} margin of error` : e?.status === "complete" ? "" : `${e?.completed_runs ?? 0} of ${e?.expected_runs ?? "?"} simulated evaluations done`}
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

  const changeCard = (i: ProposalItem) => {
    const on = isChosen(i.id, i.classification);
    const cid = `chg-${i.id}`;
    return (
      <li key={i.id} className={`${styles.change} ${on ? styles.changeOn : ""}`}>
        <div className={styles.changeHead}>
          <input id={cid} type="checkbox" checked={on}
            onChange={(e) => setChosen({ ...chosen, [i.id]: e.target.checked })} />
          <label htmlFor={cid} className={styles.changeTitle}>{fieldLabel(i.path)}</label>
          <span className={`${ui.pill} ${CLASS_TONE[i.classification]}`}>{CLASS_LABEL[i.classification]}</span>
        </div>
        <p className={ui.small + " " + ui.muted}>Section: {sectionLabel(i.section)}</p>
        <div className={ui.compare}>
          <div className={ui.compareSide}>
            <p className={ui.compareLabel}>Current text</p>
            <p>{i.old || "—"}</p>
          </div>
          <div className={`${ui.compareSide} ${ui.compareAfter}`}>
            <p className={ui.compareLabel}>Suggested text</p>
            <p><strong>{i.new}</strong></p>
          </div>
        </div>
        <p className={ui.small}><span className={ui.muted}>Why: </span>{i.rationale}</p>
        {isConsequential(i.classification) && !on && (
          <p className={ui.whyDisabled}>Not included by default. Tick it to include this pricing, plan or guarantee change.</p>
        )}
      </li>
    );
  };
  const contentItems = items.filter((i) => !isConsequential(i.classification));
  const consequentialItems = items.filter((i) => isConsequential(i.classification));
  const buildBlocked = selected.length === 0
    ? "Select at least one change to build a draft."
    : needsApproval && !consequentialOk
      ? "Tick the approval box above to include pricing, plan or guarantee changes."
      : null;

  return (
    <div>
      <PageHeader
        eyebrow="Stage 5"
        title="Improvement Studio"
        lead="See what simulated customers pushed back on, review suggested edits, and test a draft against your live page."
      />
      <p className={styles.banner} role="note">
        <span className={styles.bannerTitle}>Simulated results</span>
        <span>Changes are prepared as a draft and tested on the same simulated panel. Nothing goes live without your click and a gain of at least {a?.threshold ?? 3} points.</span>
      </p>
      {error && <Notice tone="error">{error}</Notice>}
      {!a && !error && <Loading>Loading your improvement workspace…</Loading>}

      {a && (a.status === "none" || OUTCOME[a.status]) && (
        <section className={styles.section} aria-labelledby="start-h">
          <SectionHead id="start-h" title={a.status === "none" ? "Start improving" : "Result"} />
          {OUTCOME[a.status] && (
            <Notice tone={a.status === "promoted" ? "success" : "info"}>
              {OUTCOME[a.status]} {a.reason && REASON[a.reason] ? REASON[a.reason] : ""}
            </Notice>
          )}
          {a.status === "promoted" && a.source_version && (
            <p className={ui.muted} style={{ marginBottom: 12 }}>
              Changed your mind? <button className={`${ui.button} ${ui.secondary} ${ui.buttonSm}`} disabled={busy}
                onClick={() => act(() => rollbackSite(id, a.source_version!), "Rollback failed.")}>Roll back to version {a.source_version}</button>
            </p>
          )}
          <p className={ui.sectionHelp}>Uses the latest complete simulated evaluation of your live page. If you haven’t run one yet, start in the <Link href={`/b/${id}/personas`} style={{ textDecoration: "underline" }}>Persona Arena</Link>.</p>
          <div className={ui.actions}>
            <button className={ui.button} disabled={busy} onClick={() => act(() => startAttempt(id), "Could not start.")}>
              {busy ? "Starting…" : a.status === "none" ? "Find improvements" : "Start a new improvement round"}
            </button>
          </div>
        </section>
      )}

      {a && a.opportunities.length > 0 && !OUTCOME[a.status] && (
        <section className={styles.section} aria-labelledby="opp-h">
          <SectionHead id="opp-h" step={1} title="What simulated customers pushed back on"
            help={`Based on ${a.original?.completed_runs ?? 0} of ${a.original?.expected_runs ?? "?"} simulated evaluations of your live page. The number is how many times the concern came up.`} />
          <ol className={styles.objections}>
            {a.opportunities.map((o) => (
              <li key={o.id} className={styles.objection}>
                <span className={styles.objCount} aria-label={`${o.evidence_count} mentions`}>{o.evidence_count}</span>
                <span className={styles.objTitle}>
                  {objectionLabel(o.objection_type)} · {sectionLabel(o.section)}{" "}
                  <span className={`${ui.pill} ${CLASS_TONE[o.classification]}`}>{CLASS_LABEL[o.classification]}</span>
                </span>
                <span className={styles.objExample}>“{o.example || "—"}”</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {a?.status === "awaiting_answers" && (
        <section className={styles.section} aria-labelledby="q-h">
          <SectionHead id="q-h" step={2} title="A few facts only you know"
            help="We never invent facts. Answer what you can; anything you leave undecided stays unchanged on your page." />
          {a.questions.length === 0 && <EmptyState title="No facts needed">These changes don’t depend on anything only you know.</EmptyState>}
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
                    <button className={`${ui.button} ${ui.secondary}`} disabled={busy}
                      onClick={() => act(() => answerQuestion(id, q.id, null, true), "Could not save.")}>Skip this question</button>
                    {saved && <span className={ui.small} role="status">{saved.status === "answered" ? "Answer saved" : "Skipped: this part of your page stays as it is"}</span>}
                  </div>
                </div>
              );
            })}
          </div>
          <div className={ui.actions}>
            <button className={ui.button} disabled={busy || unanswered.length > 0}
              onClick={() => act(() => generateProposal(id), "Could not prepare changes.")}>
              {busy ? "Preparing suggestions…" : "Prepare suggested changes"}
            </button>
            {unanswered.length > 0 && <span className={ui.whyDisabled}>Answer or skip {unanswered.length} more question{unanswered.length === 1 ? "" : "s"} to continue.</span>}
          </div>
        </section>
      )}

      {a?.status === "proposed" && (
        <section className={styles.section} aria-labelledby="p-h">
          <SectionHead id="p-h" step={3} title="Review suggested changes"
            help="Tick the changes you want in the draft. Your live page is not touched yet: the draft is built and tested first." />
          {items.length === 0 && <EmptyState title="No changes were suggested">Reject this round and try again later.</EmptyState>}
          {contentItems.length > 0 && (
            <>
              <h3 className={ui.h3}>Content improvements</h3>
              <ul className={styles.changes}>{contentItems.map(changeCard)}</ul>
            </>
          )}
          {consequentialItems.length > 0 && (
            <>
              <h3 className={ui.h3}>Pricing, plan and guarantee changes</h3>
              <Notice tone="warn" title="These need your explicit approval">
                They affect what customers pay or are promised, so they are off by default.
              </Notice>
              <ul className={styles.changes}>{consequentialItems.map(changeCard)}</ul>
            </>
          )}
          {needsApproval && (
            <label className={styles.approve}>
              <input type="checkbox" checked={consequentialOk} onChange={(e) => setConsequentialOk(e.target.checked)} />
              <span>I’ve reviewed the pricing, plan or guarantee changes I ticked above and approve adding them to the draft. Nothing goes live until I promote the draft.</span>
            </label>
          )}
          <div className={ui.actions}>
            <button className={ui.button} disabled={busy || !!buildBlocked}
              onClick={() => act(() => approveItems(id, selected.map((i) => i.id), consequentialOk), "Could not approve.")}>
              Build and test draft ({selected.length} change{selected.length === 1 ? "" : "s"})
            </button>
            <button className={`${ui.button} ${ui.danger}`} disabled={busy} onClick={() => act(() => rejectProposal(id), "Could not reject.")}>Reject all suggestions</button>
            {buildBlocked && <span className={ui.whyDisabled}>{buildBlocked}</span>}
          </div>
        </section>
      )}

      {a && ["approved_for_draft", "draft_built", "draft_evaluated"].includes(a.status) && (
        <section className={styles.section} aria-labelledby="d-h">
          <SectionHead id="d-h" step={4} title={`Draft${a.draft_version ? ` (version ${a.draft_version})` : ""} compared with your live page`}
            help="Demand scores are simulated. The draft only replaces your live page if it scores clearly higher." />
          {a.status === "approved_for_draft" && <Loading>Building the draft. Your live page is not touched.</Loading>}
          {a.status === "draft_built" && (
            <Loading>Testing the draft on the same simulated panel: {a.draft?.completed_runs ?? 0} of {a.draft?.expected_runs ?? "?"} simulated evaluations done.</Loading>
          )}
          <div className={styles.scoreRow}>
            <Score label="Live page demand score" e={a.original} />
            <Score label="Draft demand score" e={a.draft} />
            {a.delta_demand != null && (
              <div className={styles.score}>
                <div className={styles.scoreLabel}>Difference</div>
                <div className={styles.scoreValue}>{a.delta_demand > 0 ? "+" : ""}{a.delta_demand.toFixed(1)}</div>
                <div className={styles.scoreSub}>needs at least +{a.threshold} to go live</div>
              </div>
            )}
          </div>
          <p className={styles.note}>{a.note}</p>
          {a.status === "draft_evaluated" && (
            <div className={ui.actions}>
              <button className={ui.button} disabled={busy || a.promote_requested} onClick={() => act(() => promoteDraft(id), "Promotion refused.")}>
                {a.promote_requested ? "Making it live…" : "Make the draft live"}
              </button>
              <button className={`${ui.button} ${ui.secondary}`} disabled={busy || a.promote_requested} onClick={() => act(() => discardDraft(id), "Could not discard.")}>Discard draft, keep my live page</button>
            </div>
          )}
        </section>
      )}
    </div>
  );
}
