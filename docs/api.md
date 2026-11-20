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

## POST /public/contact

The public contact form accepts `name` (1-100 characters), `email`, `message` (10-2000
characters), and an empty `website` honeypot. The `/api/public/contact` alias is used by
the frontend through the normal API proxy. Valid submissions are stored before SMTP delivery;
delivery failures return `502` while retaining the row with `sent_ok=false`. Requests are
limited to 3 per IP per rolling hour and 10 per rolling day.

## POST /api/businesses/{id}/retry  (rate limited)

Moves a FAILED business back to the stage that failed; the worker then resumes from there.
Returns the same body as GET. Errors: `404`, `409` (not FAILED, or retry limit reached).

```
curl -s -X POST localhost:8000/api/businesses/<uuid>/retry
```

## Business names (M4A: FR-BR-1..5)

Errors on these routes use `{"detail": {"code": "...", "message": "..."}}`. The business UUID is
the capability, as above. Algorithm and weights: `docs/names.md`.

- `POST /api/businesses/{id}/names/generate` (rate limited). Body empty or `{}`; inputs come from
  the stored analysis. Needs status `GATED` **and** a persisted `go`/`go_with_changes` verdict,
  otherwise `409 not_eligible`. At most 3 successful generations (`409 generation_limit`).
  Regeneration replaces unselected candidates only. Optional `Idempotency-Key` header
  (1-100 of `[A-Za-z0-9._:-]`): a repeat returns the stored result with `replayed: true` and no
  model call; the same key for a different business or body is `422 idempotency_key_reused`.
  `502 generation_failed` if the model call fails (not counted).
- `GET /api/businesses/{id}/names`: candidates (selected first, then by rank, then invalid ones)
  with `slug, rank, score_total, score_factors, valid, invalid_reason, rationale,
  subdomain_available, subdomain_host, selected, domain_checks[]`.
- `POST /api/businesses/{id}/names/{candidate_id}/availability` (rate limited). Read-only check of
  `GODADDY_TLDS` for one of the top 5 valid candidates (`422 not_in_top_five` /
  `invalid_candidate`). Statuses: `AVAILABLE` ("Likely available — verify on GoDaddy"),
  `UNAVAILABLE`, `UNKNOWN` (with `reason`: `not_configured`, `timeout`, `rate_limited`, ...).
  `raw_price` is the registrar's raw value; units unverified, not a quote. Cached 10 minutes.
- `POST /api/businesses/{id}/names/{candidate_id}/select?replace=false` (rate limited). Claims
  `<slug>.<BASE_DOMAIN>`: `GATED -> DOMAIN_PENDING`. Same candidate again is a no-op
  (`changed: false`). Another selected: `409 already_selected` unless `replace=true` (one
  transaction; on conflict the old claim stays). `409 slug_claimed` if another business holds
  the slug, `422 invalid_candidate`, `404` for another business's candidate,
  `503 subdomain_not_configured` while `BASE_DOMAIN` is empty (no state change).

DOMAIN_PENDING is a pause stage until a deploy request: `next_stage` is `null`.

## Site deployment (M4B: FR-SITE-1..6)

Architecture, verification levels and configuration: `docs/deploy.md`. Errors use
`{"detail": {"code": "...", "message": "..."}}`.

- `POST /api/businesses/{id}/deploy` (rate limited). Needs status `DOMAIN_PENDING` and a
  claimed name. Moves to `DEPLOYING` (`202`, `changed: true`); the worker generates, publishes
  and verifies the site. Repeating while `DEPLOYING`/`LIVE` returns `200` with `changed: false`.
  Errors:
  - `404 not_found`
  - `409 not_eligible` (any other status; FAILED uses `/retry`)
  - `409 no_claim`
  - `503 deploy_not_configured` (`DEPLOY_PROVIDER` or `BASE_DOMAIN` empty)
  - `503 deploy_misconfigured` (unsafe settings, e.g. the mock provider in production)

  On any 503 nothing changes.
- `GET /api/businesses/{id}/site`: `host`, `url` (only once a version is live), `live_version`,
  `verification_level` (`simulated` / `local` / `public`), `publicly_reachable` (true only for
  `public`), and `versions[]` (`version, status, content_hash, artifact_hash, parent_version,
  generation_attempt, error, deployed_at`). No content or artifacts are returned.
- `GET /api/businesses/{id}/deployments`: deployment history, one entry per attempt
  (`version, attempt, status, verification_level, publicly_reachable, error, checks[]`).

LIVE is a pause stage until M5. Simulated or local verification is labelled as such and is
never reported as publicly reachable.

### Internal: `GET /internal/sites/tls-allowed?domain=<host>`

Not under `/api` and not in the OpenAPI schema; for Caddy on-demand TLS only (see
`docs/deploy.md`). Requires `Authorization: Bearer <INTERNAL_TLS_ASK_TOKEN>` (constant-time
comparison). A token in the query string is ignored. Empty bodies.

| Response | When |
| --- | --- |
| `200` | Exactly one `domain` that is a claimed `<slug>.<BASE_DOMAIN>` whose business is DEPLOYING or LIVE |
| `403` | Any other host (wildcard, uppercase, trailing dot, port, other apex, unclaimed), or any request carrying `Forwarded`, `X-Forwarded-For`, `X-Forwarded-Host` or `X-Real-IP` |
| `401` | Missing or wrong token |
| `400` | Zero or several `domain` parameters |
| `404` | `INTERNAL_TLS_ASK_TOKEN` is empty (endpoint disabled) |

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

Need a real Postgres database whose name ends in `_test`, owned by the dedicated `noventra_test`
role (never the app or superuser role). With the compose stack running, from the repo root:

```
TDB="$(grep '^TEST_DATABASE_URL=' .env | cut -d= -f2-)"
docker compose run --rm --no-deps -T -v "$PWD:/repo:ro" -w /repo/backend \
  -e FIXTURES_DIR=/repo/fixtures -e MOCK_LLM=1 -e TEST_DATABASE_URL="$TDB" \
  -e PYTHONDONTWRITEBYTECODE=1 api python -m pytest -q -p no:cacheprovider
```
