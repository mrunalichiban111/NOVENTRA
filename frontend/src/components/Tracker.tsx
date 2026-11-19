import type { Business, Status } from "@/lib/api";
import ui from "./ui.module.css";

const STEPS: { status: Status; name: string; doing: string }[] = [
  { status: "INTAKE", name: "Intake", doing: "Accepting your idea" },
  { status: "PREMORTEM", name: "Pre-mortem and economics", doing: "Running the pre-mortem, extracting assumptions, computing the scorecard" },
  { status: "GATED", name: "Verdict", doing: "Verdict ready" },
];

export default function Tracker({ business }: { business: Business }) {
  const { status, pipeline } = business;
  const failedAt = status === "FAILED" ? pipeline.failed_stage : status === "REJECTED" ? "PREMORTEM" : null;
  const current = failedAt ?? status;
  const index = Math.max(0, STEPS.findIndex((s) => s.status === current));
  const finished = status === "GATED";

  return (
    <section className={ui.card} aria-label="Pipeline progress">
      <h2 className={ui.h2}>Progress</h2>
      <ol className={ui.steps}>
        {STEPS.map((s, i) => {
          let cls = ui.step;
          let mark = "Waiting";
          if (finished || i < index) { cls += ` ${ui.stepDone}`; mark = "Done"; }
          else if (i === index) {
            if (failedAt) { cls += ` ${ui.stepFail}`; mark = status === "REJECTED" ? "Rejected" : "Failed"; }
            else { cls += ` ${ui.stepActive}`; mark = pipeline.in_progress ? "Working…" : "Queued"; }
          }
          return (
            <li key={s.status} className={cls}>
              <div className={ui.stepName}>{s.name}</div>
              <div className={ui.small}>{mark}</div>
            </li>
          );
        })}
      </ol>
      {!finished && !failedAt && (
        <p className={ui.muted}>{STEPS[index].doing}. This page updates every 2 seconds.</p>
      )}
      <ul className={ui.log}>
        {pipeline.history.slice(-4).map((h) => (
          <li key={h.id}>
            {new Date(h.ts).toLocaleTimeString()} · {h.action}
            {h.reason ? `: ${h.reason}` : ""}
          </li>
        ))}
      </ul>
    </section>
  );
}
