"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { createBusiness, type NewIdea } from "@/lib/api";
import samples from "@/lib/samples.json";
import ui from "./ui.module.css";

// Same rule as the API: 2-5 sentences. The server is still the authority.
function sentenceCount(text: string): number {
  return text.trim().split(/(?<=[.!?])\s+/).filter((s) => s.trim()).length;
}

export default function IdeaForm() {
  const router = useRouter();
  const [idea, setIdea] = useState("");
  const [market, setMarket] = useState("India");
  const [price, setPrice] = useState("");
  const [budget, setBudget] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(payload: NewIdea) {
    setBusy(true);
    setError(null);
    try {
      const created = await createBusiness(payload);
      router.push(`/b/${created.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Something went wrong.");
      setBusy(false);
    }
  }

  function onSubmit(ev: React.FormEvent) {
    ev.preventDefault();
    const n = sentenceCount(idea);
    if (n < 2 || n > 5) {
      setError(`Describe your idea in 2 to 5 sentences (you wrote ${n}).`);
      return;
    }
    const payload: NewIdea = { idea_text: idea.trim(), market: market.trim() || "India" };
    if (price !== "") payload.price_guess = Number(price);
    if (budget !== "") payload.starting_budget = Number(budget);
    void submit(payload);
  }

  return (
    <>
      <form className={ui.form} onSubmit={onSubmit}>
        <label className={ui.label}>
          Your business idea
          <span className={ui.hint}>2 to 5 sentences: who it is for, what you sell, how you earn.</span>
          <textarea
            className={ui.textarea}
            value={idea}
            maxLength={2000}
            onChange={(e) => setIdea(e.target.value)}
            required
          />
        </label>
        <div className={ui.row}>
          <label className={ui.label}>
            Market
            <input className={ui.input} value={market} maxLength={80} onChange={(e) => setMarket(e.target.value)} />
          </label>
          <label className={ui.label}>
            Price guess (₹, optional)
            <input className={ui.input} type="number" min="0" step="any" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
          </label>
          <label className={ui.label}>
            Starting budget (₹, optional)
            <input className={ui.input} type="number" min="0" step="any" inputMode="decimal" value={budget} onChange={(e) => setBudget(e.target.value)} />
          </label>
        </div>
        {error && <p className={ui.error} role="alert">{error}</p>}
        <div>
          <button className={ui.button} type="submit" disabled={busy}>
            {busy ? "Submitting…" : "Assess my idea"}
          </button>
        </div>
      </form>

      <div className={ui.card} style={{ marginTop: 28 }}>
        <h2 className={ui.h2}>Or try a sample idea</h2>
        <p className={ui.muted} style={{ marginBottom: 12 }}>
          One click submits the idea with its stored starting budget.
        </p>
        <div className={ui.samples}>
          {samples.map((s) => (
            <button
              key={s.id}
              type="button"
              className={`${ui.button} ${ui.ghost}`}
              disabled={busy}
              onClick={() =>
                void submit({ idea_text: s.idea, market: "India", starting_budget: s.starting_budget })
              }
            >
              {s.label}
            </button>
          ))}
        </div>
      </div>
    </>
  );
}
