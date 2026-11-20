"use client";

import { useCallback, useState } from "react";
import { retryBusiness } from "@/lib/api";
import AssumptionsTable from "./AssumptionsTable";
import PreMortem from "./PreMortem";
import Scenarios from "./Scenarios";
import Scorecard from "./Scorecard";
import Tracker from "./Tracker";
import ui from "./ui.module.css";
import NextAction from "./workspace/NextAction";
import { useWorkspace } from "./workspace/WorkspaceProvider";
import { VERDICT_LABEL } from "@/lib/format";

// Loading, load errors and polling belong to the workspace provider; this renders the assessment.
export default function BusinessView() {
  const { id, business, update } = useWorkspace();
  const [actionError, setActionError] = useState<string | null>(null);

  // `update` also restarts the provider's polling, because the retried business is in progress.
  const retry = useCallback(async () => {
    try {
      update(await retryBusiness(id));
      setActionError(null);
    } catch (e) {
      setActionError(e instanceof Error ? e.message : "Retry failed.");
    }
  }, [id, update]);

  const rethink = business?.verdict === "rethink";

  return (
    <div>
      {actionError && <p className={ui.error} role="alert">{actionError}</p>}

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

          <NextAction business={business} />

          <Scorecard business={business} />
          <Scenarios business={business} />
          <AssumptionsTable business={business} onUpdated={update} />
          <PreMortem business={business} />
        </>
      )}
    </div>
  );
}
