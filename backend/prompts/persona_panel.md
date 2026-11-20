Task: create a panel of exactly 30 SIMULATED customer personas for the business idea in the data block. The panel is used to test how real visitors might react to the business's landing page. It is a simulation, not market research.

Use only the idea, market, target customer and price/budget context in the data block. You do not see the website; do not describe it.

Each persona is a plausible, specific person in the stated market:
- name: a first name only (unique across the panel).
- segment: the customer group they belong to (short).
- occupation and age (18-80).
- budget_tier_inr: monthly spending they could put toward this kind of offer, one of under_500, 500_2k, 2k_5k, 5k_15k, over_15k.
- fit: how well they match the target customer: target, adjacent, weak or non_target.
- urgency, skepticism, tech_comfort: integers 1 (low) to 5 (high).
- hidden_objection: the doubt they would not say out loud (max 120 characters).
- constraints: up to 2 practical limits (time, location, diet, family, budget), 80 characters each.

Diversity rules (all required; these targets are deliberately stricter than the minimum checks):
- Aim for exactly 30 personas (never more than 30; a panel is only usable with at least 25, so do not stop early).
- Count before you return: write the personas one after another, number them 1 to 30 in your head, make sure each number is used exactly once with no gap, and stop at 30. Do not pad with near-duplicates to reach 30. Do NOT output numbers or ids; the array holds 30 objects.
- Mostly target and adjacent customers, but at least 5 personas with fit weak or non_target.
- At least 6 personas with skepticism 4 or 5.
- Spread budgets: at least 4 different budget_tier_inr values, and no tier used by more than 10 personas.
- Every name unique. Vary age, occupation, urgency and tech comfort. Avoid stereotypes and avoid copies of the same person.
- Respect every field limit above: hidden_objection max 120 characters, each constraint max 80, at most 2 constraints, name max 40, segment and occupation max 60.

Do not include ids, statistics, real people, brands or links. Plain text only.
