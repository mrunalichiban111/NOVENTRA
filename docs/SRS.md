# NOVENTRA: Software Requirements Specification and Roadmap

Version 1.1 (draft) · Prepared for Mrunal · Claude x GoDaddy Hackathon, Round 2 · Build phase Nov 13-20, 2026

*NOVENTRA is a working name and can change. Update this document after the kickoff webinar, when the full brief and evaluation criteria are shared.*

## 1. Introduction

### 1.1 Purpose

This document defines what NOVENTRA is, what it must do, how it will be built, and how the 5 working days of the build phase will be spent. It is the single plan to work from.

### 1.2 Scope

NOVENTRA is a web platform where a first-time founder submits a business idea and receives (a) an evidence-backed launch decision, (b) if the idea passes, a live single-page site on a real domain, and (c) a measured, automatically improved version of that site, tested against simulated customers. It is not a business-in-a-box. Logistics, fulfilment, marketing execution and legal registration are out of scope (Section 15).

### 1.3 Definitions

| Term | Meaning |
| --- | --- |
| Idea | The founder's 2-5 sentence description of the business |
| Pre-mortem | Structured adversarial analysis of why the idea could fail |
| Persona panel (arena) | Fixed set of 30 simulated customers who evaluate a page |
| Demand score (D) | Simulated conversion signal from the panel, 0-100 |
| Economics score (E) | Financial viability from the finance engine, 0-100 |
| Risk score (R) | Inverse of weighted risk severity from the pre-mortem, 0-100 |
| Release | A versioned, deployed build of a generated site |
| Gate | A point where the system blocks until a human approves |
| Simulated | Produced by models, not measured from real customers |

### 1.4 Hackathon context

- Challenge: build agentic business solutions leveraging Claude and GoDaddy technologies to help entrepreneurs, creators and small businesses go from idea to launch. As understood from the kickoff, the app need not be agentic; the expectation is an app built with Claude. NOVENTRA uses Claude both to build it (Claude Code) and inside the product, so it fits either reading. The organizers also expect a working app that is ready to use, not a prototype (Section 2.8). Confirm the rest in the HackerEarth Q&A.
- Build phase Nov 13-20; the kickoff webinar was held Nov 13. Submission is due Nov 20, 2026 on HackerEarth: live link, GitHub repository and project brief.
- Hard requirements: (1) a live app on GoDaddy Node.js hosting or a Docker virtual server; (2) your own domain bought on godaddy.com, with DNS pointing at the app and the site loading over HTTPS; (3) a mailbox such as hello@yourdomain.com that receives a test email; (4) a working contact form whose messages reach the mailbox, with no passwords in code or the repository.
- Shortlisted participants get $100 Claude credits and GoDaddy credits of $25 or $40, depending on the hosting option (Node.js Hosting or Virtual Server with Docker). Details on accessing the credits come by email and on HackerEarth. The Claude credits can be used freely, including for Claude Code.
- Customer ID and hosting choice go through the organizers' registration form. Wait for organizer confirmation before any purchase on GoDaddy.
- Scores decide which 10 of roughly 150 projects advance. The top 10 are announced Nov 30 and present on site in Pune on Dec 11.

### 1.5 Evaluation rubric and plan alignment

Impact and Demo together carry 70% of the score, so they decide where build time goes.

| Criterion | Weight | What judges look for | How NOVENTRA responds |
| --- | --- | --- | --- |
| Impact | 35% | Real-world potential, who benefits, whether people could use it | First-time founders in India get an evidence-based launch decision before spending money; judges can try it live; optional Hindi and Marathi |
| Demo | 35% | Quality and functionality of the working demo and how well it shows the impact | A live link that works for strangers, instant cached examples, the live arena, before/after on a real HTTPS subdomain |
| Tech usage | 15% | Creative, meaningful use of Claude and GoDaddy beyond a basic integration | Claude for pre-mortem, personas and rewrites with validated structured output; GoDaddy VPS, domain, DNS API and mailbox |
| Execution | 15% | Code quality, design and security patterns, a solid and repeatable build | docker compose, tests on the finance engine and scoring, secrets only in environment variables, rate limits, README |

## 2. Product Overview

### 2.1 Problem statement

Between idea and launch, small founders struggle with market validation, financial planning, regulatory hurdles, founder overload, capital, operations and early marketing. NOVENTRA addresses validation, financial planning, regulatory risk awareness and prioritization (what to do next). It surfaces capital, operations and marketing as risks but does not solve them.

### 2.2 Vision and pitch

*Other tools help you start a business. NOVENTRA tells you whether you should, before you spend a rupee, and then improves the launch with evidence.* Every other tool launches your idea; NOVENTRA tries to kill it first, then launches only the version that survived.

### 2.3 Users

- Primary: first-time founders, creators and small shop owners with an idea and no technical skills.
- Secondary: hackathon judges, who evaluate the system through the demo and documentation.

### 2.4 Positioning against GoDaddy Airo

Based on public documentation, GoDaddy Airo covers generation and setup: domain, site, logo, social posts, marketing calendar, policy documents. NOVENTRA focuses on judgment and measured improvement: should this launch, what does a skeptical market say, did the rewrite actually help. NOVENTRA does not compete on logos, social handles or marketing content. Re-verify Airo's feature set before the demo, since it ships new agents often.

### 2.5 Differentiators

1. A system that can say no, with numbers (the Rethink verdict).
2. A visual persona arena showing 30 simulated customers converting or bouncing, with reasons.
3. Measured before/after on a live domain, with automatic rollback when a change makes things worse.
4. A deterministic finance engine: the LLM extracts assumptions, plain Python does all arithmetic, and the user can edit every assumption.
5. Real engineering: idempotent versioned deploys, approval gates, audit log.

### 2.6 Constraints and assumptions

- About 5 working days; 2 build days are lost to travel (dates TBD).
- Assumed solo developer. Update if a teammate joins.
- Stack: FastAPI, Next.js, Postgres, Docker, Caddy. LangGraph is optional: a plain sequential pipeline with a status column is enough, since agentic structure itself is not scored. Persona evaluations use the Google Gemini API; everything else uses Claude.
- Hosting: Virtual Server with Docker (Option B), because the stack is Python-heavy and needs a worker plus scheduler-style jobs. Confirm which option carries which GoDaddy credit.
- Budget: $100 Claude credits, $25 or $40 GoDaddy credits. The Claude credits can be used for both Claude Code and runtime calls; Section 16 gives the split.
- GoDaddy API access for hackathon accounts is unknown (availability, DNS) and must be confirmed on Day 1. The project's own domain is bought manually on godaddy.com after organizer confirmation.
- All persona-derived numbers are simulated and must be labeled as such everywhere they appear.

### 2.7 MVP success criteria

- One idea goes from submission to a live HTTPS site on a subdomain of the project's own domain with only approval clicks from the user, in 15 minutes or less (excluding DNS and TLS propagation).
- At least 3 held-out test ideas (one clearly good, one clearly bad, one borderline) are processed, with the bad one rejected and the results recorded.
- A v1 to v2 improvement is measured on the same panel, with the rollback path demonstrated.
- A cached demo mode works without any external API call, and judges can use the live link without logging in: example ideas return instantly, while rate limits and a daily spend cap protect the credits.
- Live link, GitHub repository and project brief are submitted on HackerEarth with buffer before Nov 20, along with the README and architecture diagram. The demo video is a fallback.

### 2.8 What ready to use means

The organizers expect a working app, not a prototype. For NOVENTRA that means:

- Any visitor can submit their own new idea and get real results end to end, with no help from you. Cached examples only add speed and never stand in for the live path.
- Every stage fails visibly and recoverably: clear errors, retries, resume after a crash, no stuck runs.
- A founder can come back later: each business has a private, unguessable link that the app can email to them.
- A founder can use the output: signups appear in the dashboard, the scorecard exports, and the site can be downloaded as a static ZIP to host anywhere.
- The app is safe to leave running: idea moderation, rate limits, a spend cap, expiry of unclaimed sites, an abuse-report link, backups and a health check, kept alive until the finale.
- It has the basics of a real product: a mobile-friendly UI, a short privacy and terms page, and a working contact form.

Section 4.12 turns this into requirements. Reliability and completeness of the core journey come before breadth of features.

## 3. System Architecture

### 3.1 Components

| Component | Responsibility |
| --- | --- |
| Next.js dashboard | Idea intake, pipeline tracker, scorecard, live arena, versions, approvals, audit log |
| FastAPI service | REST API, SSE streaming, orchestration entry points, public waitlist endpoint |
| Pipeline orchestrator (LangGraph optional) | Pipeline state machine; state persisted in Postgres |
| Worker | Background jobs: domain checks, deploys, panel runs |
| Postgres | Business state, assumptions, scorecards, versions, panel runs, approvals, audit log |
| Caddy | Serves generated sites from versioned directories, automatic HTTPS |
| GoDaddy API | Domain availability, purchase (gated), DNS records |
| Anthropic API and Google Gemini API | Claude: pre-mortem, assumption extraction, names, site content, rewrites, next actions. Gemini: persona evaluations |

### 3.2 Flow

```
Idea -> Pre-mortem + assumptions -> Finance engine -> Pre-launch scorecard (E, R)
     -> Verdict gate (Rethink stops here)
     -> Name/domain shortlist -> APPROVAL -> Site generation -> Deploy + DNS + TLS
     -> Persona arena on live page -> Post-deploy scorecard (D, E, R)
     -> Improvement agent -> v2 deploy -> Re-test -> keep or roll back
     -> Final report (before/after scorecard, top 3 next actions)
```

### 3.3 Business status machine

INTAKE, PREMORTEM, GATED, DOMAIN\_PENDING, DEPLOYING, LIVE, TESTING, IMPROVING, DONE, REJECTED, FAILED. Every transition is written to the audit log.

### 3.4 Model usage

Claude (Claude Sonnet 5.5) runs the pre-mortem, assumption extraction, content generation, rewrites and next-action text. The persona arena runs on a Google Gemini Flash-class model, so a different model family judges the page than the one that wrote it; this reduces self-preference bias but is a mitigation, not proof. All model calls go through one provider interface with the model chosen per role in configuration. If Gemini rate-limits, personas fall back to a small Claude model (Claude Haiku 5.5), and the reverse. Use the paid Gemini tier for the live app so users' ideas are not used to improve Google products. Confirm model names, pricing and limits in each provider's docs on Day 1. All outputs use structured JSON validated against a schema, with up to 2 retries.

### 3.5 Deployment layout

One VPS running docker-compose with services: api, worker, postgres, frontend, caddy. Generated sites live at `/srv/sites/<domain>/releases/vN` with a `current` symlink. Caddy serves `current`; rollback means repointing the symlink.

## 4. Functional Requirements

Priority: M = must, S = should, C = could.

### 4.1 Intake (FR-IN)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-IN-1 | Accept an idea of 2-5 sentences plus optional market (default India), price guess and starting budget | M |
| FR-IN-2 | Create a business record with a unique ID and the status machine in 3.3 | M |
| FR-IN-3 | If the idea is too vague, ask one clarifying question instead of proceeding | S |
| FR-IN-4 | Provide sample ideas for one-click demo runs | S |

### 4.2 Pre-mortem (FR-PM)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-PM-1 | Produce structured output: target customer, 3 competitors, top 3 failure reasons, compliance flags, risk severities (1-5, with justification) for each risk category, suggested pivots | M |
| FR-PM-2 | Validate against the schema (Appendix C); retry up to 2 times; fail gracefully with a clear error | M |
| FR-PM-3 | Compliance flags always carry a verify-with-official-sources note and are never presented as legal advice | M |
| FR-PM-4 | Competitor information is labeled unverified unless backed by a retrieved source | M |

### 4.3 Finance engine (FR-FIN)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-FIN-1 | LLM extracts assumptions as low/base/high ranges with a one-line rationale: unit price, variable cost per unit, fixed monthly cost, startup cost, monthly visitors, visitor-to-order conversion | M |
| FR-FIN-2 | User can view and edit every assumption; edited values are tagged source=user | M |
| FR-FIN-3 | All arithmetic runs in deterministic Python, never in the LLM: contribution margin, break-even units per month, break-even month, 6-month cumulative cash, runway against starting budget | M |
| FR-FIN-4 | Three fixed scenarios (pessimistic, base, optimistic) | M |
| FR-FIN-5 | Monte Carlo over the ranges (about 2,000 draws, triangular distributions) giving P(profitable by month 6) and P10/P50/P90 cash | S |
| FR-FIN-6 | After the arena run, derive the conversion assumption as panel intent rate multiplied by an intent-to-action discount (default 5%-20%, user-editable), clearly labeled as an assumption, since stated intent overstates real behavior | M |
| FR-FIN-7 | Every displayed number shows its assumption source (llm, user, panel) | M |

### 4.4 Brand and domain (FR-BR)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-BR-1 | Generate at least 40 name candidates | M |
| FR-BR-2 | Check availability concurrently through the GoDaddy API with rate-limit handling, timeouts and retries; preferred TLDs .in, .com, .co | M |
| FR-BR-3 | Rank by length, pronounceability heuristic, no hyphens or digits; show top 5 with price | M |
| FR-BR-4 | Availability search is read-only. NOVENTRA never buys domains; the project's own domain is bought once, manually, after organizer confirmation | M |
| FR-BR-5 | Serve generated sites as subdomains of the project's own domain, using a wildcard DNS record with on-demand HTTPS or one A record per site created through the GoDaddy API | M |
| FR-BR-6 | Warn on similarity to a short list of well-known brands | C |

### 4.5 Site generator and deployer (FR-SITE)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-SITE-1 | LLM outputs structured content JSON for the fixed sections in Appendix B, never raw HTML | M |
| FR-SITE-2 | Render static, responsive HTML from a Jinja template with basic accessibility | M |
| FR-SITE-3 | Versioned release directories, atomic symlink swap, idempotent deploy keyed on business, version and content hash | M |
| FR-SITE-4 | Create the site's subdomain A record via the GoDaddy API (or rely on a wildcard record), poll until it resolves, confirm TLS, then health check (HTTP 200 and content hash match) | M |
| FR-SITE-5 | Rollback by repointing the symlink in under 10 seconds, audit-logged | M |
| FR-SITE-6 | Security headers (CSP), no scripts originating from LLM output | M |
| FR-SITE-7 | Waitlist form posting to the API and Postgres, with honeypot field and rate limiting | S |
| FR-SITE-8 | The proof section must never contain fabricated testimonials, reviews or statistics | M |

### 4.6 Persona arena (FR-AR)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-AR-1 | Generate a 30-persona panel from the target customer: segment, budget, urgency, skepticism, tech comfort, one hidden objection; stored once per business and reused across all versions | M |
| FR-AR-2 | Each persona reads the page's visible text in section order and returns a structured evaluation (Appendix C) | M |
| FR-AR-3 | Each persona is evaluated 3 times per version at nonzero temperature; aggregate per-persona and overall rates | M |
| FR-AR-4 | Parallel execution (about 10 concurrent) with retries and per-call timeouts; full run of 90 evaluations targets 3 minutes or less | M |
| FR-AR-5 | Stream results to the dashboard over SSE as they arrive | M |
| FR-AR-6 | Persist every raw evaluation | M |
| FR-AR-7 | Report objection clusters and which sections triggered them | S |
| FR-AR-8 | Label all arena output as simulated in the UI and exports | M |

### 4.7 Improvement agent (FR-IM)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-IM-1 | Cluster objections into at most 5 themes mapped to template sections | M |
| FR-IM-2 | Propose rewrites only for implicated sections, at most 3 sections per iteration so changes can be attributed | M |
| FR-IM-3 | Deploy as the next version, re-run the same panel, keep the new version only if D improves by at least 3 points (default, tunable); otherwise roll back and log why | M |
| FR-IM-4 | At most 2 iterations per run (cost cap) | M |
| FR-IM-5 | Any price change beyond 10% requires approval | M |
| FR-IM-6 | Side-by-side diff of old and new content | S |

### 4.8 Scorecard and verdict (FR-SC)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-SC-1 | Pre-launch scorecard (E and R) before any deploy, followed by the verdict gate | M |
| FR-SC-2 | Post-deploy scorecard adds D and recomputes E | M |
| FR-SC-3 | Verdict (Go, Go with changes, Rethink) per Section 6, with explanation lines naming which score drove it | M |
| FR-SC-4 | Rethink halts the pipeline; the user may override with an explicit, logged confirmation | S |
| FR-SC-5 | Final report lists the top 3 next actions in priority order | M |
| FR-SC-6 | Export the before/after scorecard as an image or PDF | S |

### 4.9 Dashboard (FR-UI)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-UI-1 | New idea page and pipeline stage tracker | M |
| FR-UI-2 | Scorecard view with editable assumptions and visible sources | M |
| FR-UI-3 | Live arena: 30 cards flipping to convert or bounce with a one-line reason | M |
| FR-UI-4 | Version history with before/after chart and rollback button | M |
| FR-UI-5 | Approvals inbox and audit log view | M |
| FR-UI-6 | Demo mode toggle that replays cached runs without external calls | M |

### 4.10 Approvals and audit (FR-GT)

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-GT-1 | Site takedown and large price changes create an approval request and block until decided | M |
| FR-GT-2 | Every automated action writes an audit entry: actor (agent or user), action, reason, version, timestamp | M |
| FR-GT-3 | Per-business kill switch disables auto-apply of improvements | S |

### 4.11 Hackathon deliverables and public use (FR-HK)

These requirements come from the kickoff submission slide and the judging rubric.

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-HK-1 | NOVENTRA runs on the GoDaddy Docker virtual server and is served over HTTPS on the project's own domain, bought on godaddy.com | M |
| FR-HK-2 | A mailbox such as hello@ the project domain exists and has received a test email | M |
| FR-HK-3 | The contact form on NOVENTRA's own site delivers messages to that mailbox over SMTP; credentials come only from environment variables | M |
| FR-HK-4 | No secrets in the repository: `.env` ignored, `.env.example` committed, history scanned before the first push | M |
| FR-HK-5 | Public use is protected: no login, per-IP and per-session rate limits, a daily Claude spend cap with a friendly message, one concurrent run per visitor | M |
| FR-HK-6 | Example ideas are computed once and served from a cache, so judges see results instantly without Claude calls | M |
| FR-HK-7 | Accept the idea in Hindi or Marathi and return the scorecard and site content in the same language | S |

### 4.12 Production readiness (FR-PR)

These requirements define what ready to use means for NOVENTRA (see 2.8).

| ID | Requirement | Pri |
| --- | --- | --- |
| FR-PR-1 | A visitor can submit a brand-new idea and complete the full pipeline live. Cached examples are shortcuts only and never replace the live path | M |
| FR-PR-2 | Each business has a private, unguessable link, remembered in the browser and emailed on request over SMTP | M |
| FR-PR-3 | Idea moderation: the pre-mortem step refuses illegal, deceptive or harmful businesses (for example scams, phishing, impersonation) and logs the refusal | M |
| FR-PR-4 | Every generated site carries an abuse-report link that reaches the mailbox; the operator can take a site down | M |
| FR-PR-5 | Per-visitor limits (for example 3 live sites at a time) and expiry of unclaimed sites after a set period (default 30 days) | M |
| FR-PR-6 | Signups from a generated site appear in the dashboard and export as CSV | M |
| FR-PR-7 | Failed stages show a retry button, runs resume after a restart, and no run stays stuck past a timeout | M |
| FR-PR-8 | A short privacy and terms page: ideas are processed by Claude and Gemini, generated sites are public, what is stored and for how long | M |
| FR-PR-9 | Health endpoint, an uptime check and a daily Postgres backup | M |
| FR-PR-10 | Mobile-responsive UI throughout | M |
| FR-PR-11 | New signups are emailed to the founder | S |
| FR-PR-12 | Download a generated site as a static ZIP | S |
| FR-PR-13 | Serve generated sites from a separate domain from the app if the credits allow, so abuse on a generated site cannot get the main app domain flagged | S |

## 5. Data Requirements

| Table | Key fields |
| --- | --- |
| businesses | id, idea\_text, market, target\_customer, status, domain, created\_at |
| assumptions | business\_id, key, low, base, high, rationale, source (llm, user, panel) |
| scorecards | id, business\_id, version, stage (pre, post), demand, economics, risk, overall, verdict, details\_json |
| domain\_candidates | business\_id, name, tld, available, price, score |
| site\_versions | id, business\_id, version, content\_json, content\_hash, path, status, parent\_version, deployed\_at |
| persona\_panels | id, business\_id, personas\_json |
| persona\_runs | id, panel\_id, site\_version\_id, persona\_id, run\_idx, decision, confidence, objection, section, reasoning |
| improvement\_attempts | id, business\_id, from\_version, to\_version, clusters\_json, changes\_json, delta\_demand, outcome (accepted, rolled\_back) |
| approvals | id, business\_id, action\_type, payload\_json, status, decided\_at |
| audit\_log | id, business\_id, actor, action, reason, version\_ref, ts |
| waitlist\_signups | id, business\_id, email, created\_at |

Store the minimum personal data (waitlist email only). Do not store API keys or secrets in the database.

## 6. Scoring and Verdict Specification

All formulas below are initial defaults. Calibrate them on Day 4 using held-out ideas, and record what was changed and why. Never tune thresholds to hit a desired demo result.

- **Demand score D** = 100 x (converted evaluations / total evaluations) across 30 personas x 3 runs. Report the mean and the standard error of per-persona rates.
- **Economics score E** = 100 x P(profitable by month 6) from the Monte Carlo. If Monte Carlo is cut, E = 100 x (0.25 x pessimistic + 0.50 x base + 0.25 x optimistic), where each scenario counts 1 if cumulative cash is at least 0 by month 6, else 0.
- **Risk score R** = 100 x (1 - (weighted mean severity - 1) / 4). Severities (1-5) come from the pre-mortem; the weighted mean uses market saturation 0.30, operational complexity 0.25, compliance 0.20, capital need 0.15, founder load 0.10. The LLM assigns severities; Python computes the score.
- **Overall (post-deploy)** = 0.40 D + 0.35 E + 0.25 R. **Overall (pre-launch)** = 0.58 E + 0.42 R.
- **Verdict:** Go if overall is at least 65 and no component is below 40. Go with changes if overall is 45-65, or overall is at least 65 but one component is below 40. Rethink if overall is below 45 or E is below 10.
- **Improvement acceptance:** keep v(N+1) only if D rises by at least 3 points; otherwise roll back.

## 7. External Interfaces and API

### 7.1 External

- Anthropic API: pre-mortem, extraction, generation and rewrite calls. Google Gemini API (paid tier): persona evaluations. Log tokens per call for both and enforce a per-run cap.
- GoDaddy API (verify endpoints in the docs): domain availability check (read-only) and DNS record create or update; the project's own domain is bought manually.
- Caddy: reload configuration when a new domain is added; automatic certificates require the DNS record to resolve first.

### 7.2 Internal REST endpoints

- `POST /businesses`, `GET /businesses/{id}`
- `POST /businesses/{id}/premortem`, `POST /businesses/{id}/finance`, `PATCH /businesses/{id}/assumptions`
- `POST /businesses/{id}/domains/search`
- `POST /approvals/{id}/decide`
- `POST /businesses/{id}/deploy`, `POST /businesses/{id}/rollback`
- `POST /businesses/{id}/panel/run`, `GET /businesses/{id}/panel/stream` (SSE)
- `POST /businesses/{id}/improve`
- `GET /businesses/{id}/scorecards`, `GET /businesses/{id}/audit`
- `POST /public/waitlist/{domain}`

## 8. Non-Functional Requirements

| Area | Requirement |
| --- | --- |
| Performance | Pre-mortem within 30s; finance engine within 2s; 40 domain checks within 45s; deploy within 60s excluding DNS and TLS; arena run within 3 min |
| Reliability | Timeouts, retries with backoff, idempotent deploys, resumable pipeline after a crash, cached demo mode |
| Cost | Measure tokens on Day 1 and set a per-business-run cap (design target about $2, to be validated); alert at 50% and 80% of the $100 budget; split build, runtime testing and a reserve for judges and the finale as in Section 16 |
| Security | Secrets in environment variables only; LLM output never rendered as raw HTML or script; CSP on generated sites; rate limits on public endpoints; idea text and page text treated as untrusted data in prompts (prompt-injection awareness); personas have no tool access |
| Transparency | Simulated outputs labeled everywhere; assumptions and their sources always visible; model-derived claims marked unverified |
| Observability | Structured logs with run IDs; token and latency metrics per call |
| Maintainability | One-command startup with docker compose; README with setup; typed schemas shared between API and UI |

## 9. Safety, Approvals and Autonomy

| Action | Autonomy |
| --- | --- |
| Copy edits within template sections | Automatic, versioned, rollback available |
| Price change over 10% | Approval required |
| Any spend (NOVENTRA does not buy domains) | Always approval, plus organizer confirmation |
| Taking a site down or deleting | Always approval |
| Overriding a Rethink verdict | Explicit confirmation, logged |

## 10. Testing and Validation

- **Unit tests:** finance engine against hand-computed cases; scoring formulas; schema validation and retry; template rendering with maximum-length content.
- **Deploy tests:** idempotency (same deploy twice changes nothing), rollback, TLS and health check, behavior when DNS is slow.
- **Panel stability:** evaluate the same page twice; scores must agree within tolerance. If the standard error is too large, increase runs per persona.
- **End-to-end:** 3 held-out ideas (clearly good, clearly bad, borderline). Include a homemade-food idea as a realistic small-business case. Record real numbers; do not rerun until the result looks good.
- **Human agreement check:** show v1 and v2 (randomized order) to 5-8 people and ask which they would sign up for and why. Compare direction with the panel and report the raw agreement count with an honest small-sample caveat.
- **Failure injection:** simulate GoDaddy and Anthropic API failures; the system must fall back to demo mode or fail with a clear message.

## 11. Roadmap (5 Working Days)

Days are working days, not calendar dates. If travel splits them, keep one invariant: the main branch deploys cleanly at the end of every working day. Do not start risky refactors the day before travel.

### Day 1: Foundations (kickoff day)

- Kickoff is done: the criteria and submission requirements are captured in Sections 1.4, 1.5 and 12.2. Still to confirm: GoDaddy API access, whether the $100 covers Claude Code, and which two days are travel days (Section 14).
- Submit Customer ID and hosting choice through the registration form; do not purchase anything until organizers confirm.
- Create the repo with a short CLAUDE.md, a `.gitignore` for secrets, and this SRS exported as `docs/SRS.md` (Section 16).
- Provision the VPS: Docker, docker-compose (api, worker, postgres, frontend, caddy), firewall, a test HTTPS page.
- After confirmation: buy the project domain on godaddy.com, point DNS at the VPS, confirm HTTPS, create the mailbox and send it a test email.
- Smoke-test the GoDaddy API: an availability check and a subdomain A record write.
- Scaffold environment handling, the pipeline skeleton with stub steps, Postgres migrations (Section 5), the provider interface for Claude and Gemini, and the structured-output helper with validation and retry.
- Measure the token cost of one persona evaluation and one generation call, check usage in Claude Code, and set the per-run cap and budget split (Section 16).
- **Exit:** the app serves HTTPS on the project domain; a stub pipeline runs end to end and writes to Postgres; the mailbox receives a test email; GoDaddy API capabilities are known.

### Day 2: Front half of the pipeline

- Intake API and form; pre-mortem agent with schema; assumption extraction.
- Finance engine in Python with unit tests; pre-launch scorecard and verdict gate.
- Name and subdomain pipeline (read-only availability search).
- Template, content generator, deployer (release directory, symlink, Caddy, subdomain DNS, health check).
- Contact form on NOVENTRA's own site, sending to the mailbox over SMTP with credentials from environment variables.
- **Exit:** idea in, pre-launch scorecard, pick a subdomain, live HTTPS site out (ugly is acceptable); a contact form message arrives in the mailbox.

### Day 3: Persona arena

- Panel generation, evaluation prompt and schema, parallel runner with retries, aggregation and persistence.
- SSE streaming and the live arena UI; demand score; economics recomputed with the intent-to-action discount; post-deploy scorecard.
- Variance test on a live page.
- **Exit:** the arena runs on a live page in 3 minutes or less with stable results and a streaming UI.

### Day 4: Improvement loop and freeze

- Objection clustering, rewrite proposals limited to 3 sections, versioned deploy, re-test, accept or roll back.
- Diff view, approvals inbox, audit log view, before/after chart.
- Run the 3 held-out ideas, record the numbers, calibrate thresholds, build cached demo mode, per-IP rate limits and the daily spend cap.
- Record a fallback demo video as soon as the loop works.
- **Feature freeze at the end of the day.**
- **Exit:** the full demo spine works end to end; a fallback video exists.

### Day 5: Validate, harden, submit

- Human agreement check with 5-8 people.
- Production readiness pass (Section 4.12): moderation, per-visitor limits and expiry, privacy and terms page, abuse-report link, backups and health check. Fix flaky stages; tighten timeouts and retries; scorecard export and site ZIP export if time allows.
- Check the live link as a stranger would: incognito window, phone, example ideas loading from cache, and the rate limit and spend cap behaving.
- README, architecture diagram, setup instructions and the project brief, including a short account of how Claude Code was used to build it; re-record the demo video if the product improved.
- Rehearse the live demo at least 3 times.
- Submit on HackerEarth with buffer before Nov 20: live link, GitHub repository, project brief.
- **Exit:** the submission package is complete and verified; the VPS, domain and some Claude credit are set to last through the Dec 11 finale.

### Cut order if behind

1. Monte Carlo (use the 3 scenarios). 2. Depth of the compliance section. 3. Name-ranking sophistication. 4. Scorecard export. 5. Waitlist form. 6. Diff view polish.

**Never cut:** the live domain, the persona arena, before/after with rollback, the verdict gate, and the basic finance engine.

## 12. Demo and Submission Plan

### 12.1 Three-minute demo script

1. Submit a clearly bad idea (for example, a general marketplace competing head-on with giants). The pre-mortem and finance engine return Rethink with reasons and numbers.
2. Submit a strong idea. Show the scorecard and its editable assumptions; pick the name and subdomain.
3. The site goes live on its own HTTPS subdomain; open it on a phone.
4. The arena runs: 30 cards flip with reasons; show baseline D and the top objections.
5. The improvement agent rewrites sections, deploys v2, re-tests; show the score change and the diff. Show a rollback of a change that did not help.
6. Show the final scorecard, the audit log and the top 3 next actions.
7. State plainly that demand and conversion figures are simulated pre-launch signals, backed by the human agreement check, and that real traffic data would feed the same loop.

Choose demo ideas by running them beforehand and pre-seeding the results honestly; do not script outcomes.

### 12.2 Submission checklist (HackerEarth, due Nov 20)

- Live link on the project's own HTTPS domain, usable without login
- GitHub repository with README, one-command setup and no secrets (`.env.example` only; scan history before the first push)
- Project brief, including the Claude Code build account from Section 16
- Mailbox on the project domain that has received a test email
- Contact form that delivers to that mailbox
- Architecture diagram and a short write-up
- Demo video and the fallback video (extras)
- Any other field the HackerEarth submission page asks for

## 13. Risks and Mitigations

| Risk | Mitigation |
| --- | --- |
| GoDaddy DNS or availability API restricted | Test on Day 1; fall back to a manually created wildcard DNS record with on-demand HTTPS |
| DNS or TLS propagation delays | Poll with timeouts; pre-deploy the demo domain well in advance |
| LLM nondeterminism makes scores noisy | Fixed panel, 3 runs per persona, report standard error, stability test |
| Simulated demand lacks credibility | Label as simulated, show reasoning, human agreement check, intent-to-action discount |
| Scope too large for 5 days | Cut order in Section 11; feature freeze at end of Day 4 |
| Travel removes build days | Deployable main branch daily; fallback video recorded on Day 4 |
| Claude credit overrun | Per-run caps, token logging, alerts at 50% and 80% |
| Live demo failure | Cached demo mode, fallback video, rehearsal |
| Prompt injection through idea or page text | Treat as data, structured outputs, no tool access for personas |
| Airo overlap perceived by judges | Position on validation and measured improvement; do not demo logo, social or marketing features |

## 14. Open Questions (resolve at or right after kickoff)

| Question | Why it matters |
| --- | --- |
| Which GoDaddy APIs do hackathon accounts get (DNS write, availability)? | Decides subdomain automation vs manual DNS |
| Which hosting option carries the $25 vs $40 credit? | Confirms the Option B choice |
| Is it acceptable to run the persona evaluations on a non-Claude model? | Judges may expect Claude in the live product; the design falls back to Claude personas if needed |
| Do the GoDaddy credits cover the mailbox, the domain and ideally a second domain for generated sites? | Both are required deliverables |
| Must the app be agentic, or is built with Claude enough? | Understood at kickoff as not required; confirm in the HackerEarth Q&A. The design fits either way |
| What does the Themes tab say about inclusion? | May shape the Impact story and the Hindi and Marathi feature |
| Which two days are travel days? | Reorder risky work before travel |
| Solo or team? | Splits work and adjusts scope |
| Is Airo reachable via API? | Possible build-on-GoDaddy story |

## 15. Out of Scope and Future Work

**Out of scope for the 5-day MVP:** ongoing traffic monitoring and scheduler, tracking snippet analytics, marketing content and social posting, logos and branding assets, payments and order management, multi-page sites, user accounts and multi-tenancy, email automation, legal advice, logistics and fulfilment.

**Future work:** feed real traffic and signups into the same loop, calibrate the persona panel against real outcomes, channel experiments, payment links, Airo integration.

## 16. Build Process with Claude Code and Credit Budget

Claude has two separate jobs in this project, and each gets its own key, budget and rules.

### 16.1 Two roles of Claude

| Aspect | Build time | Run time |
| --- | --- | --- |
| What it does | Claude Code writes, tests and refactors the code | NOVENTRA's pre-mortem, assumption, content and rewrite calls on Claude; persona evaluations on Gemini |
| Who calls it | You, from the terminal or editor | The FastAPI service and the worker |
| Credentials | Your Claude Code login, or a dedicated build key | Separate runtime API keys (Claude and Gemini), stored only in environment variables on the VPS |
| Spend control | Check usage after every session | Per-run cap, daily spend cap, per-IP rate limit |

When Claude Code runs against API credits it is billed by token consumption (see the [Claude Code cost docs](https://code.claude.com/docs/en/costs.md)). If the credits sit in a Console organization, use one workspace for build and one for runtime and set a spend limit on each, so a runaway test loop cannot drain the demo budget.

### 16.2 Credit budget

A starting split of the $100, to revise after the Day 1 measurements:

| Bucket | Amount | Covers |
| --- | --- | --- |
| Build with Claude Code | Up to $55 | Coding sessions on Days 1-4 |
| Runtime development and tests | $15 | Prompt iteration, first full runs and the 3 test ideas on Claude; persona calls go to Gemini, which Google bills separately |
| Judges and finale reserve | $25 | Claude calls from live use until Nov 30 and the Dec 11 finale |
| Buffer | $5 | Surprises |

The credits can be used freely, including for Claude Code, so build draws from the same $100. If you also have a separate Claude plan, build on that instead and move the build share to the judges and finale reserve.

### 16.3 Claude Code workflow

1. Keep a short CLAUDE.md: stack, test commands, folder layout, conventions, and the never-do list (no secrets in code, no raw HTML from model output). Export this SRS to `docs/SRS.md` and cite requirement IDs, for example FR-FIN-3, in prompts.
2. One module per session, in roadmap order. Start each session with a fresh context, ask for a plan first, then implement. Small, specific prompts cost less than open-ended ones.
3. Write tests before code for the deterministic parts (finance engine, scoring, template rendering) and let Claude Code implement until they pass.
4. Run one session at a time. Subagents and agent teams each carry their own context, so token use multiplies; avoid them on this budget.
5. Use the strongest model for architecture and tricky bugs and a cheaper one for routine edits and boilerplate.
6. Check usage after every session and keep a running log of dollars spent; compare it against the split in 16.2.
7. Commit after every working slice so a bad session is one revert away. Review every diff yourself, especially auth, secrets, SMTP and deploy code.
8. Keep runtime prompts and schemas in versioned files (`prompts/`, `schemas/`) so Claude Code edits them like code and tests catch regressions.

### 16.4 Runtime cost controls

- Pre-mortem, generation and rewrites run on the stronger Claude model; personas run on a Gemini Flash-class model on the paid tier.
- Put the page text first and the persona description last in each persona prompt so the shared prefix can be cached across the 90 evaluations; confirm cache hits in the usage fields on Day 1.
- Cap output tokens on every call and keep structured outputs short.
- Development uses recorded fixtures and a `MOCK_LLM` switch, so unit and UI tests make no Claude calls. Keep the 3 test ideas as a small eval set for real prompt checks.
- Example ideas for judges are computed once, stored and served from the cache; only new ideas call Claude.
- Public endpoint protection: per-IP and per-session limits, a daily spend cap that returns a friendly message, and one concurrent run per visitor.
- Precompute the examples with a discounted asynchronous batch run if timing allows.

### 16.5 What to show in the submission

- The CLAUDE.md and a few real prompts from the repo.
- A short section in the project brief on how Claude Code built the app: what it produced, what you reviewed or rewrote, and how you controlled cost.
- Honest numbers: tokens and dollars per full run, taken from your usage log.

## Appendix A: Problem Traceability

| Founder problem | Addressed by |
| --- | --- |
| Inadequate market validation | FR-AR-*, FR-IM-*, FR-SC-2 |
| Poor financial planning | FR-FIN-\* |
| Regulatory and compliance hurdles | FR-PM-1, FR-PM-3 (flags only) |
| Founder overload | FR-SC-5 (top 3 next actions), the automated pipeline |
| Capital and funding gaps | Surfaced as risk and runway only |
| Operations and infrastructure | Surfaced as risk only |
| Early marketing | Out of scope (existing tools such as Airo cover it) |

## Appendix B: Site Template Specification

The site is rendered from fixed sections. The rewrite agent may edit text values only, within the limits below, with no HTML, no links injected by the model, and no invented testimonials or statistics.

| Section | Fields and limits |
| --- | --- |
| hero | headline (70 chars), subhead (160), button label (24) |
| problem | 3 pain points (100 each) |
| offer | 3 benefits: title (40), body (140) |
| how\_it\_works | 3 steps: title (40), body (120) |
| proof | honest trust elements only: founder story, process, guarantees; placeholders flagged where real proof is missing |
| pricing | up to 3 plans: name, price in INR, up to 4 bullets (80 each) |
| faq | 4-6 question and answer pairs (answer 200) |
| cta | headline (70), button label (24), form fields: email, optional name |

## Appendix C: Schemas (field lists)

- **Persona:** id, name, segment, budget, urgency, skepticism, tech comfort, hidden objection, constraints.
- **Persona evaluation output:** decision (convert or bounce), confidence (0-1), top objection, section that triggered it, one-line reason.
- **Pre-mortem output:** target customer; competitors (3); failure reasons (3); compliance flags (category, note, verify flag); risk severities (1-5) with justification for market saturation, operational complexity, compliance, capital need, founder load; suggested pivots.
- **Assumption:** key, low, base, high, rationale, source.
- **Scorecard:** version, stage, D, E, R, overall, verdict, explanation lines, top 3 next actions.
