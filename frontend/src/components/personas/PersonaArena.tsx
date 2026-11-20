"use client";

// Persona Arena (M5: FR-UI-3, FR-AR-7, FR-AR-8). Every number on this page is SIMULATED and
// comes from the API; this component computes nothing. It polls only while the panel is being
// generated or an evaluation is running (the workspace poller is idle while a business is LIVE).

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { evaluateSite, generatePanel, getPanel, getResults, type Panel, type PersonaResult, type Results } from "@/lib/personas-api";
import { objectionLabel, sectionLabel } from "@/lib/improve-labels";
import { EmptyState, Loading, Notice, PageHeader, SectionHead } from "../Page";
import ui from "../ui.module.css";
import { useWorkspace } from "../workspace/WorkspaceProvider";
import styles from "./personas.module.css";

const POLL_MS = 2000;
const ERROR_TEXT: Record<string, string> = {
  evaluator_unavailable: "The evaluation model (Claude Haiku) did not answer for some personas. Completed results are kept; retry runs only the missing ones.",
  budget_exceeded: "The AI spending limit was reached. Completed results are kept; try again later.",
  evaluator_misconfigured: "Persona evaluation is not configured to use Claude Haiku on this server.",
  release_unavailable: "This website release can no longer be reproduced exactly, so it cannot be evaluated.",
  evaluation_incomplete: "Not every simulated evaluation finished.",
};

function message(e: unknown, fallback: string): string {
  if (e instanceof ApiError) return e.message || fallback;
  return e instanceof Error ? e.message : fallback;
}

function Card({ p }: { p: PersonaResult }) {
  const last = p.runs[p.runs.length - 1];
  const converts = p.runs.filter((r) => r.decision === "convert").length;
  const state = p.completed === 0 ? styles.cardPending
    : converts === p.completed ? styles.cardConvert
    : converts === 0 ? styles.cardBounce : styles.cardMixed;
  const tone = p.completed === 0 ? "Not evaluated yet"
    : converts === p.completed ? "Would sign up"
    : converts === 0 ? "Would leave" : "Mixed";
  const verdict = p.completed === 0 ? tone : `${tone} (${converts} of ${p.completed} evaluations)`;
  return (
    <li className={`${styles.card} ${state}`} aria-label={`${p.name ?? p.persona_id}: ${verdict}`}>
      <div className={styles.cardHead}>
        <span className={styles.cardName}>{p.name ?? p.persona_id}</span>
        <span className={styles.dots} aria-hidden="true">
          {p.runs.map((r) => (
            <span key={r.repeat} className={`${styles.dot} ${r.decision === "convert" ? styles.dotConvert : styles.dotBounce}`}>
              {r.decision === "convert" ? "✓" : "✕"}
            </span>
          ))}
        </span>
      </div>
      <span className={styles.cardVerdict}>{verdict}</span>
      <span className={styles.cardMeta}>{p.segment}{p.fit && p.fit !== "target" ? ` · ${p.fit.replace("_", "-")} fit` : ""}</span>
      <span className={styles.cardReason}>{last ? `“${last.reason}”` : "Waiting for this persona’s evaluation."}</span>
    </li>
  );
}

export default function PersonaArena() {
  const { id, business } = useWorkspace();
  const [panel, setPanel] = useState<Panel | null>(null);
  const [results, setResults] = useState<Results | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [tick, setTick] = useState(0);

  // One fetch per tick; ticks come from mount, actions and the poll timer below.
  useEffect(() => {
    let cancelled = false;
    Promise.all([getPanel(id), getResults(id)]).then(
      ([p, r]) => {
        if (cancelled) return;
        setPanel(p);
        setResults(r);
        setError(null);
      },
      (e) => { if (!cancelled) setError(message(e, "Could not load the persona arena.")); },
    );
    return () => { cancelled = true; };
  }, [id, tick]);

  const active =
    panel?.status === "pending" || panel?.status === "generating" ||
    results?.status === "queued" || results?.status === "running";
  useEffect(() => {
    if (!active) return;
    const t = setTimeout(() => setTick((n) => n + 1), POLL_MS);
    return () => clearTimeout(t);
  }, [active, tick]);

  async function act(fn: () => Promise<unknown>, fallback: string) {
    setBusy(true);
    try {
      await fn();
      setTick((n) => n + 1);
    } catch (e) {
      setError(message(e, fallback));
    } finally {
      setBusy(false);
    }
  }

  const live = business && business.domain && ["LIVE", "TESTING", "IMPROVING", "DONE"].includes(business.status);
  const done = results?.completed_runs ?? 0;
  const panelCount = panel?.count ?? results?.persona_count ?? 0;
  const expected = results?.expected_runs || 0;
  const repeats = panelCount > 0 && expected > 0 ? Math.round(expected / panelCount) : 0;

  return (
    <div>
      <PageHeader
        eyebrow="Stage 4"
        title="Persona Arena"
        lead="See how simulated customers react to your live page, and which parts make them hesitate."
      />
      <p className={styles.banner} role="note">
        <span className={styles.bannerTitle}>Simulated results</span>
        <span>{panelCount > 0 ? `${panelCount} AI-simulated customers read` : "AI-simulated customers read"} your live page. This is a simulation to find weak spots, not market research or real demand.</span>
      </p>

      {error && <Notice tone="error">{error}</Notice>}

      {!live && (
        <EmptyState title="Your website needs to be live first">
          The arena tests your live page. <Link href={`/b/${id}/website`} style={{ textDecoration: "underline" }}>Launch your website</Link>, then come back.
        </EmptyState>
      )}

      {live && panel && (
        <section className={styles.section} aria-labelledby="panel-h">
          <SectionHead id="panel-h" step={1} title="Simulated customer panel" />
          {panel.status === "none" && (
            <>
              <p className={ui.muted}>Create one panel of simulated customers for this business: a mix of ideal, nearby and weak-fit buyers. The same panel is reused for every version of your page, so scores stay comparable.</p>
              <div className={ui.actions}>
                <button className={ui.button} disabled={busy} onClick={() => act(() => generatePanel(id), "Could not start the panel.")}>Create simulated panel</button>
              </div>
            </>
          )}
          {(panel.status === "pending" || panel.status === "generating") && (
            <Loading>Creating your simulated customers. This can take a minute.</Loading>
          )}
          {panel.status === "failed" && (
            <>
              <Notice tone="error" title="We couldn’t create the panel">{panel.error}</Notice>
              <div className={ui.actions}>
                <button className={ui.button} disabled={busy} onClick={() => act(() => generatePanel(id), "Could not retry.")}>Try again</button>
              </div>
            </>
          )}
          {panel.status === "ready" && <p className={ui.muted}>{panel.count} simulated personas ready (a mix of target, adjacent and weak-fit customers).</p>}
        </section>
      )}

      {live && panel?.status === "ready" && results && (
        <section className={styles.section} aria-labelledby="eval-h">
          <SectionHead id="eval-h" step={2} title={`Simulated evaluation${results.site_version ? ` of site version ${results.site_version}` : ""}`} />
          {results.status === "not_started" && (
            <>
              <p className={ui.muted}>Each of the {panelCount} simulated personas reads your page text{repeats > 1 ? ` ${repeats} times` : ""}, for {expected > 0 ? expected : "several"} simulated evaluations in total.</p>
              <div className={ui.actions}>
                <button className={ui.button} disabled={busy} onClick={() => act(() => evaluateSite(id), "Could not start the evaluation.")}>Run simulated evaluation</button>
              </div>
            </>
          )}
          {(results.status === "queued" || results.status === "running") && (
            <div aria-live="polite">
              <Loading>Evaluating your page: {done} of {expected > 0 ? expected : "?"} simulated evaluations done.</Loading>
              <div className={styles.progressBar} aria-hidden="true">
                <div className={styles.progressFill} style={{ width: `${expected > 0 ? Math.round((100 * done) / expected) : 0}%` }} />
              </div>
            </div>
          )}
          {results.status === "incomplete" && (
            <>
              <Notice tone="warn" title="Evaluation incomplete">
                {done} of {expected} simulated evaluations finished. {ERROR_TEXT[results.error_code ?? ""] ?? results.error} No demand score is shown for an incomplete run.
              </Notice>
              {results.retryable && (
                <div className={ui.actions}>
                  <button className={ui.button} disabled={busy} onClick={() => act(() => evaluateSite(id, results.site_version ?? undefined), "Could not retry.")}>Retry missing evaluations</button>
                </div>
              )}
            </>
          )}
          {results.status === "complete" && results.d_score !== null && (
            <>
              <div className={styles.scoreRow}>
                <div className={styles.score}>
                  <div className={styles.scoreLabel}>Simulated demand score</div>
                  <div className={styles.scoreValue}>{results.d_score.toFixed(1)}</div>
                  <div className={styles.scoreSub}>± {results.d_se?.toFixed(1)} margin of error · {results.converted} of {expected} evaluations ended in a sign-up</div>
                </div>
              </div>
              <dl className={styles.explain}>
                <div>
                  <dt>What the demand score means</dt>
                  <dd>How readily the simulated customers chose to sign up after reading your page. Higher is better. It compares versions of your page; it does not predict real sales.</dd>
                </div>
                <div>
                  <dt>What the margin of error means</dt>
                  <dd>How much the score could move if the same test were run again. A small difference between two versions may just be noise.</dd>
                </div>
              </dl>
              <p className={styles.note}>{results.note}</p>
              <div className={ui.actions}>
                <Link href={`/b/${id}/improve`} className={ui.button}>Find improvements for this page</Link>
              </div>
            </>
          )}
        </section>
      )}

      {live && results && results.objections.length > 0 && (
        <section className={styles.section} aria-labelledby="obj-h">
          <SectionHead id="obj-h" title="What held simulated customers back" help="The most common concerns, with an example of what a simulated customer said. The big number is how many times it came up." />
          <ol className={styles.objections}>
            {results.objections.slice(0, 5).map((o) => (
              <li key={`${o.objection_type}-${o.section}`} className={styles.objection}>
                <span className={styles.objCount} aria-label={`${o.count} mentions`}>{o.count}</span>
                <span className={styles.objTitle}>{objectionLabel(o.objection_type)} · {sectionLabel(o.section)}</span>
                <span className={styles.objExample}>“{o.example || "—"}” · raised by {o.personas} persona{o.personas === 1 ? "" : "s"}, {o.bounces} left the page</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {live && results && results.personas.length > 0 && (
        <section className={styles.section} aria-labelledby="cards-h">
          <SectionHead id="cards-h" title="Individual simulated customers" />
          <p className={styles.legend}><span>Green: would sign up</span><span>Amber: mixed</span><span>Red: would leave</span><span>Dashed: not evaluated yet</span></p>
          <ul className={styles.grid}>
            {results.personas.map((p) => <Card key={p.persona_id} p={p} />)}
          </ul>
        </section>
      )}
    </div>
  );
}
