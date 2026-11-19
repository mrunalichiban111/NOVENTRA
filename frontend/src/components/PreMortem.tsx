import type { Business } from "@/lib/api";
import { RISK_LABEL } from "@/lib/format";
import ui from "./ui.module.css";

export default function PreMortem({ business }: { business: Business }) {
  const pm = business.premortem;
  if (!pm) return null;
  return (
    <section className={ui.card} aria-label="Pre-mortem">
      <h2 className={ui.h2}>Pre-mortem</h2>
      <h3 className={ui.h3}>Target customer</h3>
      <p>{pm.target_customer}</p>

      <h3 className={ui.h3}>Competitors <span className={ui.tag}>unverified</span></h3>
      <ul className={ui.list}>
        {pm.competitors.map((c, i) => <li key={i}>{c.name}</li>)}
      </ul>

      <h3 className={ui.h3}>Most likely reasons it fails</h3>
      <ol className={ui.list}>
        {pm.failure_reasons.map((r, i) => <li key={i}>{r}</li>)}
      </ol>

      <h3 className={ui.h3}>Risk ratings (1 low to 5 high)</h3>
      <div className={ui.tableWrap}>
        <table className={ui.table}>
          <thead><tr><th>Risk</th><th className={ui.num}>Severity</th><th>Why</th></tr></thead>
          <tbody>
            {Object.entries(pm.risk_severities).map(([k, v]) => (
              <tr key={k}>
                <td>{RISK_LABEL[k] ?? k}</td>
                <td className={ui.num}>{v.severity}</td>
                <td>{v.justification}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {pm.compliance_flags.length > 0 && (
        <>
          <h3 className={ui.h3}>Compliance areas to check</h3>
          <ul className={ui.list}>
            {pm.compliance_flags.map((f, i) => (
              <li key={i}><strong>{f.category}.</strong> {f.note} <em>{f.verification_note}</em></li>
            ))}
          </ul>
        </>
      )}
      <p className={ui.disclaimer}>{pm.disclaimer}</p>

      {pm.suggested_pivots.length > 0 && (
        <>
          <h3 className={ui.h3}>Suggested pivots <span className={ui.tag}>AI-suggested</span></h3>
          <ul className={ui.list}>
            {pm.suggested_pivots.map((p, i) => <li key={i}>{p}</li>)}
          </ul>
        </>
      )}
    </section>
  );
}
