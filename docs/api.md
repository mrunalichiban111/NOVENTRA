# NOVENTRA API (M1)

All routes are served under `/api`. Interactive docs: `/docs` on the API process (local only).

## Status machine (SRS 3.3)

```
INTAKE -> PREMORTEM -> GATED -> DOMAIN_PENDING -> DEPLOYING -> LIVE -> TESTING -> IMPROVING -> DONE
PREMORTEM | GATED | DOMAIN_PENDING -> REJECTED        (terminal verdict path)
any non-terminal stage -> FAILED -> (retry) -> the stage that failed
TESTING -> DONE is also allowed (no improvement needed)
```

Every transition is persisted with an `audit_log` row (actor, action, reason, version_ref, timestamp) in the
same database transaction. Retries are bounded by `MAX_STAGE_ATTEMPTS` (default 3 failures per business);
the worker never retries automatically. M1 stages are deterministic stubs (no LLM calls).

## POST /api/businesses  (rate limited)

Body: `idea_text` (required, 2-5 sentences, max 2000 chars), `market` (default `India`),
`price_guess` (>= 0), `starting_budget` (>= 0). Unknown fields are rejected.

```
curl -s -X POST localhost:8000/api/businesses -H 'content-type: application/json' \
  -d '{"idea_text":"LocalLoop pools delivery routes. Shops pay monthly.","price_guess":999}'
# 201 {"id":"<uuid>","status":"INTAKE"}
```
Errors: `422` validation, `429` rate limit.

## GET /api/businesses/{id}

Returns the business plus `pipeline`: `current_stage`, `next_stage`, `terminal`, `in_progress`, `attempts`,
`max_attempts`, `failed_stage`, `last_error`, `retryable`, and `history` (audit entries, oldest first).

```
curl -s localhost:8000/api/businesses/<uuid>
```
Errors: `404 {"detail":"Business <id> not found"}`, `422` for a malformed id.

## POST /api/businesses/{id}/retry  (rate limited)

Moves a FAILED business back to the stage that failed; the worker then resumes from there.
Returns the same body as GET. Errors: `404`, `409` (not FAILED, or retry limit reached).

```
curl -s -X POST localhost:8000/api/businesses/<uuid>/retry
```

## Worker

`python -m app.worker` polls for runnable businesses (non-terminal, not FAILED). A claim uses
`FOR UPDATE SKIP LOCKED` plus a lease (`WORKER_CLAIM_TTL_SECONDS`); a crashed worker's lease expires and
another worker resumes from the last committed stage. Each stage commits independently.

## Migrations (never run automatically)

```
cd backend && alembic upgrade head
docker compose run --rm api alembic upgrade head      # deployed stack
```

## Tests

Need a real Postgres database whose name ends in `_test`:
`TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost:5433/noventra_test pytest -q`
