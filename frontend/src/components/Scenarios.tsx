import type { Business, ScenarioResult } from "@/lib/api";
import { breakEvenMonthText, breakEvenUnitsText, money, percent, runwayText } from "@/lib/format";
import ui from "./ui.module.css";

const COLS = ["pessimistic", "base", "optimistic"] as const;

export default function Scenarios({ business }: { business: Business }) {
  const f = business.finance;
  if (!f) return null;
  const mc = f.monte_carlo;
  const rows: [string, (s: ScenarioResult) => string][] = [
    ["Contribution margin per order", (s) => money(s.contribution_margin)],
    ["Orders per month", (s) => s.monthly_orders.toFixed(1)],
    ["Monthly operating profit", (s) => money(s.monthly_operating_profit)],
    ["Cash after 6 months", (s) => money(s.six_month_cash)],
    ["Break-even volume", breakEvenUnitsText],
    ["Cost recovered (break-even month)", breakEvenMonthText],
    ["Runway", runwayText],
  ];
  return (
    <section className={ui.card} aria-label="Financial scenarios">
      <h2 className={ui.h2}>Financial scenarios</h2>
      <p className={ui.muted} style={{ marginBottom: 10 }}>
        Calculated in code from the assumptions below. Starting budget {money(f.starting_budget)}
        {f.starting_budget_source === "founder"
          ? " (from you)."
          : " (not given, so assumed equal to the high startup cost)."}
      </p>
      <div className={ui.tableWrap}>
        <table className={ui.table}>
          <thead>
            <tr><th></th>{COLS.map((c) => <th key={c} className={ui.num}>{c[0].toUpperCase() + c.slice(1)}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map(([label, fn]) => (
              <tr key={label}>
                <td>{label}</td>
                {COLS.map((c) => <td key={c} className={ui.num}>{fn(f.scenarios[c])}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className={ui.h3}>Monte Carlo <span className={ui.tag}>simulated</span></h3>
      <p>
        <strong>{percent(mc.p_profitable)}</strong> of {mc.n.toLocaleString("en-IN")} simulated runs end
        month 6 with positive cash. Month-6 cash: P10 {money(mc.p10)}, median {money(mc.p50)}, P90 {money(mc.p90)}.
      </p>
      <p className={ui.muted}>Random seed {mc.seed}, so the same assumptions always give the same result.</p>
    </section>
  );
}
