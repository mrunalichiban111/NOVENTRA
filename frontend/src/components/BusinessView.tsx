"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { getBusiness, retryBusiness, type Business } from "@/lib/api";
import AssumptionsTable from "./AssumptionsTable";
import PreMortem from "./PreMortem";
import Scenarios from "./Scenarios";
import Scorecard from "./Scorecard";
import Tracker from "./Tracker";
import ui from "./ui.module.css";
import { VERDICT_LABEL } from "@/lib/format";

const POLL_MS = 2000;
// The pipeline pauses at GATED in this milestone; these states need no more polling.
const SETTLED = new Set(["GATED", "REJECTED", "FAILED", "DONE"]);

export default function BusinessView({ id }: { id: string }) {
  const [business, setBusiness] = useState<Business | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [polling, setPolling] = useState(true);

  useEffect(() => {
    if (!polling) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout>;
    async function tick() {
      try {
        const b = await getBusiness(id);
        if (cancelled) return;
        setBusiness(b);
        setError(null);
        if (SETTLED.has(b.status)) { setPolling(false); return; }
      } catch (e) {
        if (cancelled) return;
        setError(e instanceof Error ? e.message : "Could not load this assessment.");
      }
      timer = setTimeout(tick, POLL_MS);
    }
    void tick();
    return () => { cancelled = true; clearTimeout(timer); };
  }, [id, polling]);

  const retry = useCallback(async () => {
    try {
      setBusiness(await retryBusiness(id));
      setError(null);
      setPolling(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Retry failed.");
    }
  }, [id]);

  // Assumption edits return the full recomputed business; the pipeline is not restarted.
  const updated = useCallback((b: Business) => setBusiness(b), []);

  const rethink = business?.verdict === "rethink";

  return (
    <main className={ui.shell}>
      <Link href="/" className={ui.brand}>NOVENTRA</Link>
      {!business && !error && <p className={ui.muted}>Loading…</p>}
      {error && <p className={ui.error} role="alert">{error}</p>}

      {business && (
        <>
          <h1 className={ui.h1} style={{ fontSize: 22 }}>Your assessment</h1>
          <p className={ui.muted} style={{ marginBottom: 20 }}>{business.idea_text}</p>

          <Tracker business={business} />

          {business.status === "FAILED" && (
            <div className={ui.card}>
              <h2 className={ui.h2}>Something went wrong</h2>
              <p className={ui.error}>{business.pipeline.last_error ?? "The analysis failed."}</p>
              <div className={ui.actions}>
                {business.pipeline.retryable ? (
                  <button className={ui.button} onClick={retry}>Retry from where it stopped</button>
                ) : (
                  <span className={ui.muted}>The retry limit has been reached.</span>
                )}
              </div>
            </div>
          )}

          {business.status === "REJECTED" && (
            <div className={ui.rethinkBox}>
              <h2 className={ui.h2}>We can’t assess this idea</h2>
              <p>{business.rejection_reason}</p>
              <p className={ui.muted} style={{ marginTop: 8 }}>
                This is a safety refusal, not a verdict on the idea’s commercial merit.
              </p>
            </div>
          )}

          {rethink && business.scorecard && (
            <div className={ui.rethinkBox}>
              <h2 className={ui.h2}>Verdict: {VERDICT_LABEL.rethink}</h2>
              <p>The numbers do not support launching this idea as described. The pipeline stops here.</p>
              <ul className={ui.list} style={{ marginTop: 8 }}>
                {business.scorecard.explanation.map((l, i) => <li key={i}>{l}</li>)}
              </ul>
              {business.premortem && (
                <>
                  <h3 className={ui.h3}>Main reasons</h3>
                  <ol className={ui.list}>
                    {business.premortem.failure_reasons.map((r, i) => <li key={i}>{r}</li>)}
                  </ol>
                </>
              )}
              <p className={ui.muted} style={{ marginTop: 8 }}>
                You can change the assumptions below and recalculate if you think they are wrong.
              </p>
            </div>
          )}

          <Scorecard business={business} />
          <Scenarios business={business} />
          <AssumptionsTable business={business} onUpdated={updated} />
          <PreMortem business={business} />
        </>
      )}
    </main>
  );
}
