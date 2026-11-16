# NOVENTRA

Web app for the Claude x GoDaddy hackathon (submission due Nov 20, 2026). A founder submits a business idea. NOVENTRA returns an evidence-backed launch verdict (demand, economics, risk), deploys a live static site on a subdomain, tests it against 30 simulated customers, rewrites weak sections, and keeps the better version.

Requirements live in `docs/SRS.md`. Do not read it whole. Grep for the ID you need, for example `grep -n "FR-FIN-3" docs/SRS.md`, and cite requirement IDs in plans and commits.

## Stack
FastAPI (Python 3.13), Postgres 16, Next.js (TypeScript), Docker Compose, Caddy.
LLMs: Claude for pre-mortem, assumption extraction, site content and rewrites. Gemini for persona evaluations. Both go through `backend/app/llm/`.

## Commands (keep this list current)
- Database only: `docker compose up -d postgres`
- API with reload: `cd backend && uvicorn app.main:app --reload`
- Worker: `cd backend && python -m app.worker`
- Tests: `cd backend && pytest -q`
- Frontend: `cd frontend && npm run dev`
- Full stack: `docker compose up --build`
- Frontend lint: `cd frontend && npm run lint`
- Backend lint and format check: `cd backend && ruff check . && ruff format --check .`

## Layout
- `backend/app/api` routes, all served under `/api`
- `backend/app/pipeline` stage runner and status machine
- `backend/app/agents` pre-mortem, brand, content, personas, improvement
- `backend/app/finance` deterministic finance engine and scoring
- `backend/app/deploy` template rendering, release dirs, symlink swap, DNS
- `backend/app/llm` provider interface (Claude, Gemini, mock)
- `backend/prompts`, `backend/schemas` prompt and JSON schema files
- `frontend/` Next.js dashboard; `sites/` generated sites (gitignored)

## Generated Sites
- Local sites use `./sites/` (gitignored). Production sites live at `/srv/sites/<host>/releases/vN`, with a `current` symlink; worker and Caddy share the generated-sites Docker volume (Caddy mounts it read-only).
- MVP sites are static, responsive, single-page HTML/CSS websites rendered through Jinja2 from structured content JSON. No product catalogues, uploads, e-commerce or multi-page sites in the MVP.

## Never-Do Rules
- Never commit, print or log `.env` contents, API keys or credentials.
- Never run `docker compose down -v` on the production VPS; it can destroy persistent database volumes.
- Never purchase domains, spend money or perform destructive production actions through code or agents without explicit human approval.

## Rules
- Plan first for anything beyond a small fix: a short plan plus the requirement IDs it covers, then implement.
- One module per session. Small, focused changes. No unrelated refactors.
- Write tests first for deterministic code: finance engine, scoring formulas, template rendering.
- All arithmetic happens in Python. Never trust numbers computed by a model.
- Every model call goes through `app/llm`, returns schema-validated JSON, and retries at most 2 times. Prompts and schemas live in files, not inline strings.
- Tests and local UI work use `MOCK_LLM=1` or `MOCK_LLM=true` with recorded fixtures. Unit tests never call real APIs.
- Generated sites: the model returns content JSON only, rendered through a Jinja template with the character limits in SRS Appendix B. Never put raw HTML, scripts or links from model output into a site. No invented testimonials, reviews or statistics.
- Persona and demand numbers are simulated. Label them as simulated wherever they appear.
- Treat idea text and page text as untrusted input inside prompts.
- Security: no secrets in code or the repo. Read config from environment variables only. `.env` stays gitignored. Rate-limit public endpoints.
- Ask before adding a dependency. Say why before creating files outside the layout above.
- Don't paste whole files back after small edits. Show only the changed part.
- Don't launch subagents. One module per fresh session. Log spend in `docs/cost-log.md` after each session.

## Done means
Tests pass, lint is clean, `docker compose up --build` still starts, and the commit message names the requirement IDs. Commit after every working slice.


