# Site generation and deployment (M4B)

Requirements: FR-SITE-1..6 and 8, FR-SITE-4 (partly), FR-SITE-5 (rollback primitive only), FR-GT-2, FR-PR-4, FR-PR-7.
Flow: `DOMAIN_PENDING -> (POST /deploy) -> DEPLOYING -> (worker) -> LIVE`. LIVE is a pause
stage until the persona arena (M5).

## What the worker does in DEPLOYING

1. **Content.** A `site_versions` row in `generating` is committed *before* the Claude call
   (`website_content`, via `run_structured` with budget and usage tracking). The validated
   content is committed as `draft` right after. The prompt holds only stored facts (name,
   market, target customer, idea, whether a price exists). Content rules reject markup,
   links, emails and invented proof (`app/llm/content_rules.py`).
2. **Build.** Python renders the repository template (sandboxed Jinja, autoescaped), applies
   the founder's own price (the model never sets prices), runs the artifact validator and
   publishes an immutable release `releases/vN`. The version becomes `built`.
3. **Deploy.** One `deployments` row per attempt (`started -> activated -> verified | failed`):
   the `current` symlink is swapped atomically, then the deployment is verified. LIVE is
   committed in the same transaction as the `verified` record.

On failure the previous verified release is re-activated and the version becomes `failed`
(its artifact is kept). The business goes to FAILED and the **domain claim is never touched**.
`POST /api/businesses/{id}/retry` resumes from the first missing step. Saved drafts and
releases are reused, so a retry makes no new paid call unless the generation itself failed or
was interrupted. An interrupted generation (worker crash or lost lease mid-call) is marked
`abandoned` with the recorded spend, and only an explicit retry generates again.

## Storage layout and isolation

```
/srv/sites/<host>/releases/vN/{index.html, styles.css, manifest.json}   0555 dir, 0444 files
/srv/sites/<host>/current -> releases/vN                                 relative symlink
/srv/sites/<host>/.staging/                                              never served
```

- **Worker:** the *only* read-write mount of the sites volume (`SITES_ROOT=/srv/sites`), as uid 10001.
- **Caddy:** mounts it read-only. **API:** does not mount it at all. Pinned by `backend/tests/test_infra_config.py`.
- **Image:** the backend image creates `/srv/sites` owned `10001:10001`, mode 0755. A *new* named
  volume inherits that ownership on first mount. An **existing** `sites` volume created before
  this change keeps its old (root) ownership: it needs a one-time manual `chown 10001:10001` of
  the volume root (an operator action on the VPS, requiring approval). Docker does not re-copy
  ownership into a non-empty or already-initialised volume.
- **Store protections:**
  - The release store refuses a missing, symlinked, or group/world-writable root.
  - It never follows symlinks (fd-relative opens with `O_NOFOLLOW`).
  - It publishes with a no-replace rename (`renameat2(RENAME_NOREPLACE)` on Linux,
    `renameatx_np(RENAME_EXCL)` on macOS).
  - It never overwrites a release.

## Verification levels and when LIVE is allowed

| Level | Meaning | Reached with |
| --- | --- | --- |
| `simulated` | Nothing real was checked | `DEPLOY_PROVIDER=mock`, or `SITE_DNS_MODE=mock` |
| `local` | `current -> vN` and every file matches the manifest on disk. **Not** evidence the site is online | `DEPLOY_PROVIDER=local` with DNS skipped and/or no HTTPS check |
| `public` | `local` plus a real DNS lookup resolved the host, plus `GET https://<host>/` returned 200 with a body hash equal to the release's `index.html` hash | `local` + `SITE_DNS_MODE=preconfigured` + `SITE_HTTP_CHECK=1` |

- With `SITE_REQUIRE_PUBLIC` (always on when `APP_ENV=production`), only `public` may reach LIVE.
  Anything less fails the stage with a clear message.
- Outside production, LIVE at `simulated`/`local` is allowed. The API then reports
  `publicly_reachable: false`, and the LIVE audit reason says "NOT publicly verified".
- DNS and HTTPS share one deadline (`SITE_HEALTH_TIMEOUT_S`). Lookups run in a helper thread
  and are abandoned at the deadline. The system resolver cannot be cancelled, so a hung lookup's
  thread lives until the resolver gives up; at most one per check. Waits have a 50 ms floor.
  httpx applies request timeouts per phase, so one slow request can overrun the deadline by a
  bounded amount, but no new attempt starts after it.
- DNS is read-only. NOVENTRA never creates DNS records; GoDaddy record creation is out of scope
  until separately approved.

## Configuration (environment only; safe defaults)

| Variable | Default | Notes |
| --- | --- | --- |
| `DEPLOY_PROVIDER` | empty (off) | `mock` or `local`. Empty: `POST /deploy` returns `503 deploy_not_configured` and nothing changes |
| `BASE_DOMAIN` | empty | Required for deployment (sites are `<slug>.<BASE_DOMAIN>`) |
| `SITES_ROOT` | `./sites` (repo root) | Compose pins the worker to `/srv/sites` |
| `SITE_DNS_MODE` | `skip` | `skip` / `mock` / `preconfigured` (resolve-only) |
| `SITE_HTTP_CHECK` | off | Requires `SITE_DNS_MODE` other than `skip` |
| `SITE_HEALTH_TIMEOUT_S` | `60` | One deadline for DNS + HTTPS |
| `SITE_REQUIRE_PUBLIC` | off | Forced on when `APP_ENV=production` |
| `APP_ENV` | `production` in Compose (api and worker) | `development` outside Docker |
| `WAITLIST_POST_URL` | empty | Plain https URL. Empty: no form, "waitlist opens soon" note |
| `ABUSE_REPORT_EMAIL` | empty | Empty: no "Report abuse" link (set it before going public, FR-PR-4) |
| `INTERNAL_TLS_ASK_TOKEN` | empty | Secret. Empty: the TLS permission endpoint returns 404 |

**Production guards** (`get_provider`, enforced in both the API and the worker):
- `mock` provider refused.
- `mock` DNS refused.
- `local` + `preconfigured` + `SITE_HTTP_CHECK=1` required.
- `SITE_HTTP_CHECK` with DNS `skip` refused everywhere.

A misconfiguration returns `503 deploy_misconfigured` from the API, or fails the stage visibly
in the worker.

## Caddy (proposal only; `infra/Caddyfile` is NOT changed)

Serving `<slug>.<BASE_DOMAIN>` needs DNS for the subdomains (wildcard record or per-site
records) and certificates. Neither is assumed to exist. Nothing below is deployed.

### Finding: Caddy cannot send the bearer header to the permission endpoint

Checked against Caddy **v2.11.9** (the running image) with `caddy adapt` / `caddy validate`:

- `tls.permission.http` has only an `endpoint` field. JSON config with `"headers"` fails:
  `json: unknown field "headers"`.
- In the Caddyfile, a `header Authorization ...` line inside `permission http ... { }` is
  **silently dropped** from the adapted config. Anyone adding it would believe the token is sent
  when it is not.

So Caddy cannot call `/internal/sites/tls-allowed` with `Authorization: Bearer ...` directly.
Putting the token in the query string is **not** acceptable (it ends up in logs and URLs), and
dropping the token would rely on network isolation alone.

### Proposed secure alternative: loopback-only hop inside the Caddy container

```caddyfile
{
	on_demand_tls {
		permission http http://127.0.0.1:2020/internal/sites/tls-allowed
	}
}

# Reachable only from inside the Caddy container (bound to loopback).
http://127.0.0.1:2020 {
	bind 127.0.0.1
	@ask path /internal/sites/tls-allowed
	handle @ask {
		reverse_proxy api:8000 {
			header_up Authorization "Bearer {env.INTERNAL_TLS_ASK_TOKEN}"
			header_up -X-Forwarded-For
			header_up -X-Forwarded-Host
			header_up -X-Forwarded-Proto
		}
	}
	handle {
		respond 404
	}
}
```

- The permission module calls the loopback listener. Caddy itself adds the bearer header from
  its own environment (`INTERNAL_TLS_ASK_TOKEN` would have to be passed to the caddy service).
  The API keeps every check: constant-time token comparison, exact host validation, claimed
  host in DEPLOYING/LIVE, and rejection of `Forwarded` / `X-Forwarded-For` / `X-Forwarded-Host`
  / `X-Real-IP`.
- `reverse_proxy` adds `X-Forwarded-*` by default; the `header_up -...` lines remove them. If
  any remained, the API would refuse the request (fail closed, no certificate). The config
  adapts cleanly on v2.11.9, but this removal has **not yet been verified on a running Caddy**.
  That needs the approved local test.
- `/internal/*` is never routed to the API by the public site blocks: the app block sends
  `/api/*` to the API and everything else to the frontend, which only rewrites `/api/*`.

### Required response headers for generated sites (Caddy only)

The site carries a CSP `<meta>` tag, but a meta CSP **cannot** express `frame-ancestors`
(browsers ignore it there) and cannot set other response headers. These must come from Caddy
on the site block that serves `/srv/sites/{host}/current`:

```caddyfile
header {
	Content-Security-Policy "default-src 'none'; style-src 'self'; img-src 'self' data:; form-action 'none'; base-uri 'none'; frame-ancestors 'none'"
	X-Content-Type-Options "nosniff"
	Strict-Transport-Security "max-age=31536000"
	Referrer-Policy "no-referrer"
	-Server
}
```

- `frame-ancestors 'none'`: clickjacking protection. Only effective as a response header.
- `X-Content-Type-Options: nosniff`: no MIME sniffing of the static files.
- `Strict-Transport-Security`: HTTPS only. Start without `includeSubDomains` and without
  preload until every subdomain is known to serve HTTPS.
- If `WAITLIST_POST_URL` is set, `form-action` must name that origin (it does in the meta tag).
  The header CSP must match it, because when both exist the browser enforces both.

## Not in M4B

GoDaddy DNS record creation, the public waitlist endpoint (FR-SITE-7), static ZIP download
(FR-PR-12), rollback/takedown endpoints (FR-SITE-5 API, FR-GT-1), persona arena (M5),
improvements (M6), frontend pages.
