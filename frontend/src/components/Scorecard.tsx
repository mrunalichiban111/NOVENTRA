import type { Business } from "@/lib/api";
import { VERDICT_LABEL, score } from "@/lib/format";
import ui from "./ui.module.css";

export default function Scorecard({ business }: { business: Business }) {
  const sc = business.scorecard;
  if (!sc) return null;
  const tiles = [
    { label: "Economics (E)", value: sc.economics, sub: "Simulated chance of positive cash by month 6" },
    { label: "Risk (R)", value: sc.risk, sub: "Higher is safer. From model-rated risks" },
    { label: "Overall", value: sc.overall, sub: "0.58 × E + 0.42 × R" },
  ];
  return (
    <section className={ui.card} aria-label="Pre-launch scorecard">
      <h2 className={ui.h2}>Pre-launch scorecard (version {sc.version})</h2>
      <div className={ui.verdictHead}>
        <span className={`${ui.badge} ${ui[sc.verdict]}`}>{VERDICT_LABEL[sc.verdict]}</span>
        <span className={ui.muted}>Verdict</span>
      </div>
      <div className={ui.tiles}>
        {tiles.map((t) => (
          <div key={t.label} className={ui.tile}>
            <div className={ui.tileLabel}>{t.label}</div>
            <div className={ui.tileValue}>{score(t.value)}<span className={ui.tileSub}> / 100</span></div>
            <div className={ui.tileSub}>{t.sub}</div>
          </div>
        ))}
      </div>
      <h3 className={ui.h3}>Why this verdict</h3>
      <ul className={ui.list}>
        {sc.explanation.map((line, i) => <li key={i}>{line}</li>)}
      </ul>
      {sc.next_actions.length > 0 && (
        <>
          <h3 className={ui.h3}>Next actions <span className={ui.tag}>AI-suggested</span></h3>
          <ol className={ui.list}>
            {sc.next_actions.map((a, i) => <li key={i}>{a.text}</li>)}
          </ol>
        </>
      )}
      <p className={ui.disclaimer}>
        Competitors, assumptions and risk ratings are AI-generated estimates and are unverified.
        Probabilities and cash ranges are simulated from those assumptions, not observed results.
      </p>
    </section>
  );
}
