"use client";

import { useState } from "react";
import { patchAssumptions, type Assumption, type AssumptionEdit, type Business } from "@/lib/api";
import { ASSUMPTION_LABEL } from "@/lib/format";
import ui from "./ui.module.css";

type Field = "low" | "base" | "high";
const FIELDS: Field[] = ["low", "base", "high"];

export default function AssumptionsTable({
  business,
  onUpdated,
}: {
  business: Business;
  onUpdated: (b: Business) => void;
}) {
  const editable = business.status === "GATED";
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const keyOf = (a: Assumption, f: Field) => `${a.key}.${f}`;
  const shown = (a: Assumption, f: Field) => draft[keyOf(a, f)] ?? String(a[f]);
  const isEdited = (a: Assumption, f: Field) => {
    const d = draft[keyOf(a, f)];
    return d !== undefined && d !== "" && Number(d) !== a[f];
  };

  const edits: AssumptionEdit[] = business.assumptions.flatMap((a) => {
    const changed = FIELDS.filter((f) => isEdited(a, f));
    if (changed.length === 0) return [];
    const edit: AssumptionEdit = { key: a.key };
    for (const f of changed) edit[f] = Number(draft[keyOf(a, f)]);
    return [edit];
  });

  async function save() {
    setBusy(true);
    setError(null);
    try {
      const updated = await patchAssumptions(business.id, edits);
      setDraft({});
      onUpdated(updated);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Could not save changes.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className={ui.card} aria-label="Assumptions">
      <h2 className={ui.h2}>Assumptions</h2>
      <p className={ui.muted} style={{ marginBottom: 10 }}>
        {editable
          ? "Change any value and recalculate. Recalculation is instant, uses no AI call, and your edits are tagged “user”."
          : "These values feed the calculations."}{" "}
        “llm” means an AI estimate; treat it as unverified.
      </p>
      <div className={ui.tableWrap}>
        <table className={ui.table}>
          <thead>
            <tr>
              <th>Assumption</th>
              {FIELDS.map((f) => <th key={f} className={ui.num}>{f[0].toUpperCase() + f.slice(1)}</th>)}
              <th>Source</th>
              <th>Rationale</th>
            </tr>
          </thead>
          <tbody>
            {business.assumptions.map((a) => {
              const meta = ASSUMPTION_LABEL[a.key];
              return (
                <tr key={a.key}>
                  <td>
                    {meta.name}
                    <div className={ui.small + " " + ui.muted}>{meta.unit}</div>
                  </td>
                  {FIELDS.map((f) => (
                    <td key={f} className={ui.num}>
                      {editable ? (
                        <input
                          className={`${ui.cellInput} ${isEdited(a, f) ? ui.edited : ""}`}
                          type="number"
                          min="0"
                          step="any"
                          inputMode="decimal"
                          aria-label={`${meta.name} ${f}`}
                          value={shown(a, f)}
                          onChange={(e) => setDraft({ ...draft, [keyOf(a, f)]: e.target.value })}
                        />
                      ) : (
                        a[f]
                      )}
                    </td>
                  ))}
                  <td>
                    <span className={`${ui.src} ${a.source === "user" ? ui.srcUser : ""}`}>{a.source}</span>
                  </td>
                  <td className={ui.small}>{a.rationale}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {error && <p className={ui.error} role="alert" style={{ marginTop: 12 }}>{error}</p>}
      {editable && (
        <div className={ui.actions}>
          <button className={ui.button} onClick={save} disabled={busy || edits.length === 0}>
            {busy ? "Recalculating…" : "Save and recalculate"}
          </button>
          <button className={`${ui.button} ${ui.ghost}`} onClick={() => { setDraft({}); setError(null); }} disabled={busy || Object.keys(draft).length === 0}>
            Reset
          </button>
        </div>
      )}
    </section>
  );
}
