# Business names, slugs and ranking (M4A)

Covers FR-BR-1 (40+ candidates), FR-BR-3 (deterministic ranking) and FR-BR-5 (subdomain labels).
All constants live in `backend/app/names/config.py`; change them there and note why below.

## Pipeline

1. **Generate**: `business_names` task (small Claude tier, no extended thinking) through
   `run_structured` (schema-validated, 2 repair retries, budget guard, `run_id`). The prompt
   contains only stored data: idea, market, the pre-mortem's target customer and competitors
   (the pre-mortem has no separate positioning field). All of it sits in the untrusted-data block.
2. **Normalize** (`normalize.py`): NFKC, trim, collapse whitespace, drop empty names, drop
   case-insensitive duplicates keeping the first. The rationale is cleaned the same way and is
   display-only.
3. **Slug + validate** (`slug.py`), see below. Invalid candidates are kept with a reason code.
4. **Rank** (`rank.py`): valid candidates only, scored on the slug only.

## Slug derivation (never repairs structure)

NFKD, drop combining marks (`é` → `e`), lowercase, drop apostrophes, each whitespace run → one
`-`. Hyphens already in the name are kept as they are, so `--x` stays `--x` and fails, and
`Go - File` becomes `go---file` and fails. Nothing else is transliterated: `ß`, `&`, `.` and
non-Latin scripts fail with `unsupported_char` (no IDN, no punycode).

Reason codes, checked in this order (the first failure wins):

| Code | Rule |
| --- | --- |
| `unsupported_char` | anything outside `[a-z0-9-]` after derivation |
| `punycode` | starts with `xn--` |
| `edge_hyphen` | leading or trailing `-` |
| `double_hyphen` | contains `--` |
| `too_short` / `too_long` | length outside 3–30 |
| `reserved` | in `RESERVED_SLUGS` (infrastructure, mail, auth, `noventra`, `b`, …) |
| `apex_label` | equals the first label of `BASE_DOMAIN` |
| `duplicate_slug` | an otherwise valid slug already taken by an earlier candidate in the same list |
| `legacy_unvalidated` | pre-M4 rows, set by migration 0004 only |

## Ranking (slug only; the rationale is never scored)

Each factor is a value in [0, 1] multiplied by its weight. The weights sum to 100, so `score_total`
is 0–100. Per-factor `{value, weight, points}` is stored in `score_factors` for the UI.

| Factor | Weight | Value |
| --- | --- | --- |
| length | 25 | 1.0 for 5–10 chars; −0.25 per char below 5; linear to 0 at 20 chars |
| pronounceability | 25 | mean of (a) vowel ratio (`aeiouy`) within 0.30–0.60 → 1.0, −2.0 per unit outside; (b) structure: 1 − 0.25 × letters beyond runs of 2 consonants or 2 identical letters |
| no_digits | 10 | 1 if no digits |
| no_hyphens | 15 | 1 if no hyphens |
| typeability | 15 | 1 − 0.34 per ambiguous char (`0`, `1`) or sequence (`rn`, `vv`, `cl`, `ii`) |
| keyword_overlap | 10 | 1.0 if an idea keyword (4+ letters, not a stopword) is in the slug; 0.5 if its first 4 letters are; else 0 |

Factor values are rounded to 4 places and the total to 2. Valid candidates are ranked 1..N by
`(-score_total, slug)`, so ties break alphabetically. The ranking is a heuristic for display
order only. It makes no trademark, legal-clearance or domain-availability claim.

Change log: rev 1 used 0.5 per offending run. It became 0.25 per excess letter because
counting runs ranked `strngth` above `strength`.

## Storage and claims

`domain_candidates` holds one row per candidate (`run_id, name, slug, valid, invalid_reason,
rank, score_total, score_factors, rationale, selected`). The pre-M4 columns `tld, available,
price, score` are kept for compatibility, unused by M4. Per-TLD registrar results go in
`domain_checks` (`tld, status AVAILABLE|UNAVAILABLE|UNKNOWN, reason, raw_price, currency,
definitive, checked_at`).

The subdomain claim is canonical in two partial unique indexes on `selected` rows:
`uq_domain_candidates_claimed_slug (slug)` and `uq_domain_candidates_one_selected
(business_id)`. Unselected candidates are not globally unique. Regeneration replaces unselected
candidates only. A selected candidate is only changed by the selection service (M4B).
