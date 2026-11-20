"use client";

// Persona Arena (M5: FR-UI-3, FR-AR-7, FR-AR-8). Every number on this page is SIMULATED and
// comes from the API; this component computes nothing. It polls only while the panel is being
// generated or an evaluation is running (the workspace poller is idle while a business is LIVE).

import Link from "next/link";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { evaluateSite, generatePanel, getPanel, getResults, type Panel, type PersonaResult, type Results } from "@/lib/personas-api";
import ui from "../ui.module.css";
import { useWorkspace } from "../workspace/WorkspaceProvider";
import styles from "./personas.module.css";

const POLL_MS = 2000;
const SECTION: Record<string, string> = {
  header: "Header", hero: "Hero", problem: "Problem", offer: "Offer", how_it_works: "How it works",
  proof: "About / proof", pricing: "Pricing", faq: "FAQ", cta: "Sign-up", footer: "Footer", none: "Whole page",
};
const OBJECTION: Record<string, string> = {
  missing_info: "Missing information", price: "Price", trust: "Trust", fit: "Not a fit", clarity: "Unclear", other: "Other",
};
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
  const state = p.completed === 0 ? styles.cardPending : converts * 2 >= p.completed ? styles.cardConvert : styles.cardBounce;
  const verdict = p.completed === 0 ? "Waiting" : `${converts}/${p.completed} convert`;
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
      <span className={styles.cardMeta}>{p.segment}{p.fit && p.fit !== "target" ? ` · ${p.fit.replace("_", "-")} fit` : ""}</span>
      <span className={styles.cardReason}>{last ? last.reason : "Not evaluated yet"}</span>
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
  const expected = results?.expected_runs ?? 90;

  return (
    <div>
      <h1 className={ui.h1} style={{ fontSize: 22 }}>Persona Arena</h1>
      <p className={styles.banner} role="note">
        <span className={styles.bannerTitle}>Simulated results</span>
        <span>30 AI-simulated customers read your live page. This is a simulation to find weak spots, not market research.</span>
      </p>

      {error && <div className={ui.error} role="alert">{error}</div>}

      {!live && (
        <p className={ui.muted} role="status">
          The arena tests your live website. <Link href={`/b/${id}/website`}>Launch your website</Link> first.
        </p>
      )}

      {live && panel && (
        <section className={styles.section} aria-labelledby="panel-h">
          <h2 id="panel-h" className={ui.h2}>1. Simulated customer panel</h2>
          {panel.status === "none" && (
            <>
              <p className={ui.muted}>Create one panel of 30 simulated customers for this business. It is reused for every version of your page.</p>
              <div className={ui.actions}>
                <button className={ui.button} disabled={busy} onClick={() => act(() => generatePanel(id), "Could not start the panel.")}>Create simulated panel</button>
              </div>
            </>
          )}
          {(panel.status === "pending" || panel.status === "generating") && (
            <p role="status"><span className={styles.spinner} aria-hidden="true" />Creating 30 simulated personas…</p>
          )}
          {panel.status === "failed" && (
            <>
              <p className={ui.error} role="alert">Panel creation failed: {panel.error}</p>
              <div className={ui.actions}>
                <button className={ui.button} disabled={busy} onClick={() => act(() => generatePanel(id), "Could not retry.")}>Try again</button>
              </div>
            </>
          )}
          {panel.status === "ready" && <p className={ui.muted}>{panel.count} simulated personas ready (mix of target, adjacent and weak-fit customers).</p>}
        </section>
      )}

      {live && panel?.status === "ready" && results && (
        <section className={styles.section} aria-labelledby="eval-h">
          <h2 id="eval-h" className={ui.h2}>2. Simulated evaluation{results.site_version ? ` of site v${results.site_version}` : ""}</h2>
          {results.status === "not_started" && (
            <>
              <p className={ui.muted}>Each persona reads the page text 3 times (90 simulated evaluations, Claude Haiku).</p>
              <div className={ui.actions}>
                <button className={ui.button} disabled={busy} onClick={() => act(() => evaluateSite(id), "Could not start the evaluation.")}>Run simulated evaluation</button>
              </div>
            </>
          )}
          {(results.status === "queued" || results.status === "running") && (
            <div role="status" aria-live="polite">
              <p><span className={styles.spinner} aria-hidden="true" />Evaluating… {done} of {expected} simulated evaluations done</p>
              <div className={styles.progressBar} aria-hidden="true">
                <div className={styles.progressFill} style={{ width: `${Math.round((100 * done) / expected)}%` }} />
              </div>
            </div>
          )}
          {results.status === "incomplete" && (
            <>
              <p className={ui.error} role="alert">
                Incomplete: {done} of {expected} done. {ERROR_TEXT[results.error_code ?? ""] ?? results.error} No demand score is shown for an incomplete run.
              </p>
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
                  <div className={styles.scoreLabel}>Simulated demand score D</div>
                  <div className={styles.scoreValue}>{results.d_score.toFixed(1)}</div>
                  <div className={styles.scoreSub}>± {results.d_se?.toFixed(1)} SE · {results.converted} of {expected} converted</div>
                </div>
              </div>
              <p className={styles.note}>{results.note}</p>
              <div className={ui.actions}>
                <Link href={`/b/${id}/improve`} className={ui.button}>Improve the page</Link>
              </div>
            </>
          )}
        </section>
      )}

      {live && results && results.objections.length > 0 && (
        <section className={styles.section} aria-labelledby="obj-h">
          <h2 id="obj-h" className={ui.h2}>Top simulated objections</h2>
          <ol className={styles.objections}>
            {results.objections.slice(0, 5).map((o) => (
              <li key={`${o.objection_type}-${o.section}`} className={styles.objection}>
                <span className={styles.objCount} aria-label={`${o.count} mentions`}>{o.count}</span>
                <span className={styles.objTitle}>{OBJECTION[o.objection_type] ?? o.objection_type} · {SECTION[o.section] ?? o.section}</span>
                <span className={styles.objExample}>“{o.example || "—"}” · {o.personas} personas, {o.bounces} bounces</span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {live && results && results.personas.length > 0 && (
        <section className={styles.section} aria-labelledby="cards-h">
          <h2 id="cards-h" className={ui.h2}>Simulated personas</h2>
          <ul className={styles.grid}>
            {results.personas.map((p) => <Card key={p.persona_id} p={p} />)}
          </ul>
        </section>
      )}
    </div>
  );
}
