Task: run a pre-mortem on the business idea in the data block, assuming the founder launches in the stated market (default India).

Produce:
- target_customer: one concrete sentence.
- competitors: exactly 3 plausible competitors or substitute behaviours. They are unverified and from general knowledge only; name categories or well-known players, never invent obscure companies.
- failure_reasons: exactly 3 most likely reasons this business fails, most likely first.
- compliance_flags: regulatory or licensing areas the founder should check (for example food licensing, data protection, consumer protection, GST). Every flag has verify=true. These are prompts to verify with official sources, never legal advice.
- risk_severities: integer 1 (low) to 5 (high), each with a one-sentence justification, for market_saturation, operational_complexity, compliance, capital_need, founder_load.
- suggested_pivots: 1 to 3 concrete pivots that reduce the biggest risks.
Be candid. A vague or unrealistic idea should receive high severities.
