import type { AssumptionKey, ScenarioResult, Verdict } from "./api";

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });

export const money = (n: number) => `${n < 0 ? "−" : ""}₹${inr.format(Math.abs(Math.round(n)))}`;
export const score = (n: number) => n.toFixed(1);
export const percent = (fraction: number) => `${(fraction * 100).toFixed(1)}%`;

export const VERDICT_LABEL: Record<Verdict, string> = {
  go: "Go",
  go_with_changes: "Go with changes",
  rethink: "Rethink",
};

export const ASSUMPTION_LABEL: Record<AssumptionKey, { name: string; unit: string }> = {
  unit_price: { name: "Unit price", unit: "₹ per order" },
  variable_cost_per_unit: { name: "Variable cost per unit", unit: "₹ per order" },
  fixed_monthly_cost: { name: "Fixed monthly cost", unit: "₹ per month" },
  startup_cost: { name: "Startup cost", unit: "₹ one-off" },
  monthly_visitors: { name: "Monthly visitors", unit: "visitors per month" },
  visitor_to_order_conversion: { name: "Visitor-to-order conversion", unit: "fraction, 0.02 = 2%" },
};

export const RISK_LABEL: Record<string, string> = {
  market_saturation: "Market saturation",
  operational_complexity: "Operational complexity",
  compliance: "Compliance",
  capital_need: "Capital need",
  founder_load: "Founder load",
};

export function runwayText(s: ScenarioResult): string {
  switch (s.runway_status) {
    case "self_sustaining": return "Not burning cash";
    case "finite": return `${s.runway_months} month${s.runway_months === 1 ? "" : "s"}`;
    case "beyond_horizon": return "More than 24 months";
    case "underfunded": return "0: startup cost exceeds budget";
  }
}

export const breakEvenUnitsText = (s: ScenarioResult) =>
  s.break_even_units === null ? "Not reachable (margin not positive)" : `${s.break_even_units} orders`;

export const breakEvenMonthText = (s: ScenarioResult) =>
  s.break_even_month === null ? "Not within 24 months" : `Month ${s.break_even_month}`;
