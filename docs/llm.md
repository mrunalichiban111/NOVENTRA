# LLM layer (M2)

Reusable Claude intelligence layer used by later stages (pre-mortem, assumptions, names, site
content). Claude only; Gemini and persona/rewrite tasks arrive in M5/M6. Covers FR-PM-2,
FR-PM-1, FR-FIN-1, FR-BR-1, FR-SITE-1, SRS 3.4 and 9.

## Architecture (`backend/app/llm/`)
| File | Role |
| --- | --- |
| `base.py` | `LLMProvider` protocol (one network attempt per call), request/response types, typed errors |
| `claude.py` | `ClaudeProvider` on the official `anthropic` SDK (pinned 2.10.0), SDK retries disabled |
| `mock.py` | `MockProvider`: reads `fixtures/llm/*.json`, no network, no key, cost 0 |
| `routing.py` | The single task-to-model table (`TASKS`); model ids from env only |
| `pricing.py` | Price registry keyed by model id; estimates only |
| `schemas.py` | Pydantic output schemas (Appendix B limits); validate real output and fixtures alike |
| `structured.py` | `run_structured(task, schema, prompt, run_id=...)`: validation, repair, retries |
| `budget.py`, `usage.py` | Per-run spend cap, reservations, `llm_usage` persistence, `spend_last_24h_usd()` |

Prompts live in `backend/prompts/` (`_preamble.md` plus one file per task). Fixtures live in
repo-root `fixtures/llm/`.

## Configuration
| Variable | Purpose |
| --- | --- |
| `MOCK_LLM` | `true` or `1`: use `MockProvider`; no API key, no reservations, cost 0 |
| `ANTHROPIC_API_KEY` | Real mode only. Never logged or committed |
| `CLAUDE_MODEL_STRONG` / `CLAUDE_MODEL_SMALL` | Model ids, no defaults. Expected: `claude-sonnet-5-5` / `claude-haiku-5-5` (confirmed against the models overview, 2026-11-19) |
| `PER_RUN_CAP_USD` | Per-run spend cap, default `2.00` |
| `FIXTURES_DIR` | Mock fixture root. Default is the repo-root `fixtures/`; Docker sets `/app/fixtures` |

Tasks now: `premortem`, `assumption_extraction`, `website_content` (strong) and
`business_names` (small). Each row has `max_tokens` (includes thinking), timeout and effort.
There is no temperature column: the Claude 5.x docs say `temperature`/`top_p`/`top_k` return a
400 on Sonnet 5.5, so none are sent.

## Structured output
- Each task is one `strict: true` tool (schema from the Pydantic model via `anthropic.transform_schema`)
  with `tool_choice: auto` and an instruction to call it. Sonnet 5.5 rejects *forced* tool choice
  (400), so forcing is not used. Thinking is kept minimal with `output_config.effort="low"`.
- Strict mode drops length/range keywords, so Pydantic re-validates every limit client-side.
- Fallback: if no tool call is returned, text is parsed after stripping a Markdown fence.
- Handled: valid JSON, fenced JSON, malformed JSON, empty output, missing fields, wrong types,
  and `max_tokens` truncation (treated as a failed attempt).
- Up to **2 repair retries** (3 attempts) with concise feedback (field paths and messages only,
  never the model's bad values). Final failure raises `StructuredOutputError`; nothing is ever
  filled in. Model output is data: never executed or rendered as HTML.
- Injection hygiene (SRS 9): the idea/page text goes in an `<untrusted_data>` block (a literal
  closing tag inside it is neutralised) and the system prompt says it is data, not instructions.

## Retry policy
Separate budgets: validation repairs (2) and transport tries (3 per attempt, exponential backoff
with jitter). Each real try is budget-checked first.

| Outcome | Meaning | Retried? | Reservation |
| --- | --- | --- | --- |
| `not_sent` | Failed before any bytes left (connect error/timeout, pool timeout) | yes | released |
| `rejected` | Provider answered with an error status (429/5xx retried; 4xx not) | 429/5xx only | released |
| `ambiguous` | Timeout or interruption after sending, or unknown cause | **never** | settled at worst case |
| response received (success, validation failure, refusal) | usage known | n/a | settled at actual estimate |

Ambiguous failures are the conservative case: the provider may have billed us, so the worst-case
estimate is counted permanently and recorded with `error_category=transport_ambiguous`.

## Usage tracking
Table `llm_usage` (migration `0002`): task, provider, model, attempt, token counts (input,
output, cache read/write), `estimated_cost_usd`, `latency_ms`, status, `error_category`,
`run_id`, nullable `business_id`. Rows are written in their own transaction, so they survive a
later pipeline stage failing. Failed attempts are recorded whenever usage is known. Mock mode
writes nothing.

## Pricing source and cost estimation
USD per million tokens, verified from https://platform.claude.com/docs/en/about-claude/pricing
on **2026-11-19**, for requests up to 100K input tokens (larger requests are refused):

| Model | Input | Output | Cache read | Cache write (5 min) |
| --- | --- | --- | --- | --- |
| `claude-sonnet-5-5` | 2.00 | 10.00 | 0.10 | 2.50 |
| `claude-haiku-5-5` | 0.10 | 0.50 | 0.01 | 0.125 |

`estimated_cost_usd` is **an estimate, not billing**: it is token counts times these prices. The
Console is the source of truth. A model id missing from `pricing.py` fails real calls closed.

## Budget cap (`PER_RUN_CAP_USD`)
- **A real call needs a `run_id`** (argument, or the pipeline runner's `current_run_id`). Without
  one, `RunIdRequired` is raised before anything else. Mock mode needs none.
- Before **every** real attempt (first call, transport retry, repair) the worst case is reserved:
  estimated input tokens (characters / 3, deliberately high) plus `max_tokens` times the output
  price. Settled spend plus active unexpired reservations plus that estimate must stay within
  the cap, else `BudgetExceeded` is raised with no API call.
- The reserve runs in one short transaction: a transaction-scoped `pg_advisory_xact_lock` keyed
  on `run_id`, then one atomic `INSERT ... SELECT` check. (Under READ COMMITTED an atomic insert
  alone lets concurrent attempts overshoot; a test proves the lock matters.) No transaction is
  held during the network call. After the call it is settled or released and the usage row is
  written together.
- Crash recovery is expiry only: reservations older than 5 minutes stop counting. No cleanup job.
- `spend_last_24h_usd()` exists for the M7 daily cap, which is not built yet.

## Mock mode
`MOCK_LLM=true` runs everything from `fixtures/llm/` with no key and no network. Fixtures
validate against the same Pydantic schemas as real output. Docker mounts `./fixtures` read-only at
`/app/fixtures` and sets `FIXTURES_DIR`. Tests prove zero network use: sockets are patched in
mock tests and an autouse guard fails any test that tries a real HTTP request.

## Manual smoke test (one cheap real request)
```
read -rs ANTHROPIC_API_KEY && export ANTHROPIC_API_KEY
export CLAUDE_MODEL_STRONG=claude-sonnet-5-5 CLAUDE_MODEL_SMALL=claude-haiku-5-5
export DATABASE_URL=<migrated database>      # or add --no-db
backend/.venv/bin/python scripts/llm_smoke_test.py --tier small
backend/.venv/bin/python scripts/llm_smoke_test.py --tier strong
```
It refuses to run in mock mode or without a key, uses a fresh UUID `run_id`, and prints model,
token usage and estimated cost. Run `--tier strong` too: the strict-tool plus `effort` request
shape for Sonnet 5.5 follows the docs but has not been exercised against the real API.

## Migration
`cd backend && alembic upgrade head` (docker: `docker compose run --rm api alembic upgrade head`).
Real mode needs revision `0002`; mock mode does not touch the database.

## Known limitations
- Real-API behaviour is unverified until the smoke test is run.
- `ambiguous` failures overstate spend by design; a flaky network can burn the cap on estimates.
- Timeouts are not retried (safety over convenience).
- Input size uses a characters/3 heuristic, not a tokenizer.
- Costs ignore the Batch/long-context/priority price tiers; only the standard tier is modelled.
- The default guard uses the app database (`DATABASE_URL`); an unreachable DB fails real calls.
- Competitor and compliance text from the model is unverified general knowledge (FR-PM-3/4).
