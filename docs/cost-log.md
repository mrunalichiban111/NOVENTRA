# Cost log

| Date | Session / module | Model | Spend (USD) | Notes |
| --- | --- | --- | --- | --- |
| 2026-11-18 | M1 database, status machine, resumable pipeline | Claude Sonnet 5.5 | not measured | Spend is not visible to the agent; fill in from the console. No runtime LLM calls (stubs only). |
| 2026-11-19 | M2 Claude intelligence layer | Claude Sonnet 5.5 | not measured | Spend not visible to the agent; fill in from the console. Automated tests make no real API calls. Manual smoke tests (Haiku 5.5, Sonnet 5.5) were run by the user and passed. |
| 2026-11-19 | M3 Block C live test | Claude Sonnet 5.5 (runtime) | 0.0757 (estimated, from llm_usage) | Unintended: containers had MOCK_LLM=false from .env, so 3 sample ideas made 6 real calls (premortem + assumption_extraction). Re-ran with MOCK_LLM=true. Agent session spend itself not measured. |
