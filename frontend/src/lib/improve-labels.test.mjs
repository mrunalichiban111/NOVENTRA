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
