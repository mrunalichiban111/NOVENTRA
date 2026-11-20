import assert from "node:assert/strict";
import test from "node:test";
import { fieldLabel, sectionLabel } from "./improve-labels.ts";

test("known paths map to founder-facing labels", () => {
  assert.equal(fieldLabel("pricing.0.name"), "Pricing plan name");
  assert.equal(fieldLabel("pricing.0.bullets.2"), "Pricing plan detail 3");
  assert.equal(fieldLabel("faq.2.answer"), "FAQ answer 3");
  assert.equal(fieldLabel("offer.0.title"), "Offer 1 headline");
  assert.equal(fieldLabel("offer.0.body"), "Offer 1 description");
  assert.equal(fieldLabel("proof.guarantees"), "Guarantee");
});

test("unknown paths fall back to readable words with no indices or dots", () => {
  for (const p of ["foo_bar.3.baz_qux", "pricing.1.tier_name", "x"]) {
    const l = fieldLabel(p);
    assert.doesNotMatch(l, /[._]|\d/);
  }
  assert.equal(fieldLabel("foo_bar.3.baz_qux"), "Foo bar baz qux");
});

test("section labels", () => {
  assert.equal(sectionLabel("how_it_works"), "How it works");
  assert.equal(sectionLabel("weird_thing"), "Weird thing");
});

test("answer counter shows the limit and never truncates", async () => {
  const { ANSWER_MAX, answerCounter } = await import("./improve-labels.ts");
  assert.equal(ANSWER_MAX, 300);
  assert.deepEqual(answerCounter("abc"), { label: "3 / 300 characters", over: false, message: null });
  const long = "x".repeat(305);
  const c = answerCounter(long);
  assert.equal(c.over, true);
  assert.equal(c.label, "305 / 300 characters");
  assert.match(c.message, /Shorten this answer by 5 characters/);
});
