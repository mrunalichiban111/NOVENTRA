# NOVENTRA
### From Business Idea to Evidence-Driven Launch.

**NOVENTRA is an AI-powered business validation and launch platform that helps founders evaluate, build, test, and improve business ideas before investing heavily in execution.**

Most founders move from an idea directly to building a product or website. NOVENTRA introduces a structured validation workflow between those two steps.

It combines business assessment, financial modelling, pre-mortem analysis, brand naming, website generation, simulated customer evaluation, and AI-assisted improvement into one connected pipeline.

Instead of simply telling founders whether an idea sounds promising, NOVENTRA helps them understand the risks, launch a testable version, identify weaknesses, and evaluate proposed improvements.

---

## The Problem

Early-stage founders often face several challenges:

- They invest time and money before validating whether a business idea is viable.
- Financial assumptions are frequently based on optimism rather than structured analysis.
- They struggle to identify business risks and potential failure scenarios early.
- Creating a brand identity and launch-ready website requires multiple tools and skills.
- They lack access to structured customer feedback before launching.
- Even when feedback is available, translating it into measurable improvements is difficult.

Existing tools generally solve isolated parts of this journey.

NOVENTRA aims to connect these activities into a single, continuous workflow.

## Our Solution

NOVENTRA transforms an initial business idea into an iterative validation process.

The platform guides founders through six connected stages:

1. Business assessment and pre-mortem.
2. Financial modelling and assumption analysis.
3. Brand naming and domain exploration.
4. Website generation and launch.
5. Simulated customer evaluation through AI personas.
6. AI-assisted improvement and evaluation of proposed changes.

The objective is to help founders make better-informed decisions before committing significant resources.

---

# Core Features

## 1. AI-Powered Business Assessment

Founders begin by submitting their business idea and relevant business information.

NOVENTRA analyses the idea across multiple dimensions, including:

- Target customer and market problem.
- Proposed value proposition.
- Business model and revenue assumptions.
- Pricing assumptions.
- Operational feasibility.
- Potential risks and failure scenarios.

The platform produces a structured assessment with one of three outcomes:

- **GO:** The idea demonstrates sufficient promise to proceed.
- **GO WITH CHANGES:** The idea has potential but requires specific adjustments.
- **RETHINK:** Significant weaknesses need to be reconsidered before proceeding.

The assessment is intended to support founder decision-making, not replace professional financial, legal, or market research.

## 2. Financial Modelling and Pre-Mortem Analysis

NOVENTRA helps founders examine the financial assumptions behind their ideas.

The assessment engine includes:

- Structured business assumptions.
- Revenue and cost modelling.
- Scenario analysis.
- Monte Carlo simulation.
- Risk and pre-mortem analysis.
- Financial feasibility indicators.

The Monte Carlo engine uses seeded simulations to explore uncertainty across business assumptions.

Founders can review and edit assumptions rather than treating AI-generated estimates as unquestionable facts.

The pre-mortem component examines possible reasons why a business might fail, helping founders identify risks before launch.

## 3. AI Naming Studio

Once an idea passes the assessment gate, founders can explore potential business names.

Naming Studio provides:

- AI-generated business name candidates.
- Candidate ranking and selection.
- Domain availability checks through the configured provider integration.
- Domain selection.
- NOVENTRA-hosted subdomain support.

The objective is to reduce the friction between validating an idea and establishing its initial brand identity.

Domain availability is subject to provider responses and can change over time.

## 4. Website Launch Studio

NOVENTRA generates a static business website using structured business information and AI-generated website content.

The website generation pipeline includes:

- Hero section and value proposition.
- Problem and solution presentation.
- Offer and service information.
- How-it-works sections.
- Proof and credibility content.
- Pricing information.
- Frequently asked questions.
- Calls to action.

The generated website is validated before deployment.

The deployment architecture supports versioned releases, artifact integrity checks, health verification, and rollback capabilities.

Deployment providers are configurable. Mock deployment is intended for development and testing, while local deployment supports the configured release-store workflow.

A successful mock deployment should not be interpreted as a publicly accessible production website.

## 5. Persona Arena — Simulated Customer Evaluation

A website being published does not automatically mean that customers will understand or trust the offering.

NOVENTRA introduces Persona Arena to evaluate the visible content of a generated website through simulated customer personas.

The system uses different customer profiles to examine the website from different perspectives.

Personas evaluate factors such as:

- Whether the offering is understandable.
- Whether the business addresses a relevant problem.
- Whether the value proposition is convincing.
- Whether pricing and information are sufficiently clear.
- Whether credibility and trust signals are present.
- Whether the website gives visitors a reason to convert.

The evaluation pipeline supports multiple personas and repeated evaluations.

Results are aggregated into demand-related indicators and supporting evidence, including objections, missing information, and conversion-related outcomes.

The resulting insights help founders identify which parts of their website may be preventing visitors from taking action.

**Important:** Persona Arena uses AI-simulated customer evaluations. These results are not real customer interviews, actual visitor analytics, verified market demand, or a substitute for live user testing.

## 6. Improvement Studio — AI-Assisted Iteration

Improvement Studio turns Persona Arena findings into actionable website improvements.

Rather than asking founders to manually interpret every objection, NOVENTRA uses the identified opportunities and founder-provided business context to generate proposed changes.

The workflow includes:

1. Identify key objections and missing information.
2. Collect relevant founder answers and business facts.
3. Generate proposed website improvements.
4. Allow founders to review and approve or reject individual changes.
5. Create a separate draft website version.
6. Evaluate the draft using the same persona panel and evaluation framework.
7. Compare the original and proposed versions.
8. Apply the promotion gate before accepting the improved version.

The original website is preserved while a draft is being evaluated.

The promotion decision is subject to a defined improvement threshold and comparability requirements. A draft is not automatically promoted simply because AI generated it.

This creates an iterative workflow in which proposed improvements must be evaluated rather than blindly accepted.

**Important:** A simulated improvement in persona evaluation is an indicator for further testing, not proof of an increase in real-world conversions.

---

# End-to-End Workflow

```text
              BUSINESS IDEA
                    |
                    v
          BUSINESS ASSESSMENT
                    |
                    v
        FINANCIAL MODELLING
          + PRE-MORTEM
                    |
                    v
             VERDICT GATE
                    |
          +---------+---------+
          |                   |
         RETHINK          PROCEED
                              |
                              v
                       NAMING STUDIO
                              |
                              v
                       WEBSITE STUDIO
                              |
                              v
                       WEBSITE LAUNCH
                              |
                              v
                        PERSONA ARENA
                              |
                              v
                     CUSTOMER OBJECTIONS
                              |
                              v
                     IMPROVEMENT STUDIO
                              |
                              v
                      DRAFT GENERATION
                              |
                              v
                      DRAFT EVALUATION
                              |
                              v
                       PROMOTION GATE
                              |
                   +----------+----------+
                   |                     |
                 APPROVE               REJECT
                   |                     |
                   v                     v
             NEW VERSION LIVE       ORIGINAL RETAINED
```

---

# Technology Stack

The application uses a modular architecture with separate responsibilities for the frontend, API, background processing, persistence, AI integration, and deployment.

| Layer | Technology |
|---|---|
| Frontend | Next.js 16, React 19, TypeScript |
| Backend API | Python, FastAPI |
| Database | PostgreSQL |
| ORM and migrations | SQLAlchemy, Alembic |
| Background processing | Dedicated worker |
| AI integration | Anthropic Claude API |
| Frontend styling | CSS Modules and custom design system |
| Website generation | Jinja2 templates |
| Website deployment | Versioned static release architecture |
| Containerization | Docker and Docker Compose |
| Reverse proxy | Caddy |

## Architecture

NOVENTRA follows a modular pipeline-based architecture.

**Frontend**

The Next.js application provides the founder-facing experience, including business submission, assessment results, naming, website launch, persona evaluation, and improvement workflows.

**Backend API**

FastAPI handles business operations, workflow state transitions, validation, persistence, and communication with the different application modules.

**Database**

PostgreSQL stores business information, assumptions, assessments, evaluation records, website versions, deployment information, and improvement workflows.

**Background Worker**

Long-running tasks such as AI generation, evaluation batches, and other asynchronous pipeline operations are handled by a dedicated worker.

**AI Provider Layer**

The AI integration is separated behind a provider abstraction to support structured model interactions, controlled usage, and mock-provider testing.

**Deployment Layer**

Generated websites are treated as versioned artifacts. The deployment layer supports integrity validation, release activation, health verification, and rollback workflows.

---

# Engineering and Reliability Considerations

NOVENTRA is designed around more than simply generating AI text.

Important engineering considerations include:

- Structured AI outputs and validation.
- Explicit workflow states and transitions.
- Resumable background processing.
- Auditability of important operations.
- Versioned website artifacts.
- Artifact hash verification.
- Controlled deployment activation.
- Promotion gates for website improvements.
- Separation of draft and live website versions.
- Mock providers for development and testing.
- Usage and budget controls for AI operations.

These mechanisms help make the workflow more predictable and reduce the risk of unvalidated AI-generated changes being treated as production-ready output.

---

# Getting Started

## Prerequisites

- Python 3.12 or a compatible version specified by the project.
- Node.js compatible with the Next.js application.
- Docker and Docker Compose.
- PostgreSQL, if running services outside Docker.
- Required AI and optional domain-provider credentials.

## Configuration

Create the environment configuration using the project's environment example, if available.

Configure the required database connection, application secret, AI provider credentials, and deployment settings.

Do not commit API keys, passwords, tokens, or other secrets to GitHub.

## Run with Docker Compose

From the repository root:

```bash
docker compose up -d --build
```

For a local development database that requires migrations, run:

```bash
docker compose run --rm api alembic upgrade head
```

Then start or rebuild the application services:

```bash
docker compose up -d --build api worker frontend
```

The exact environment variables, service ports, and configuration requirements are defined by the project configuration. Consult the environment example and Docker Compose files before running in a new environment.

---

# Testing

The project includes automated tests covering different parts of the system.

Areas of test coverage include:

- API and business workflow operations.
- Financial calculations and assessment logic.
- AI provider abstractions.
- Naming and website generation.
- Deployment and release validation.
- Persona evaluation.
- Improvement proposals.
- Draft evaluation and promotion logic.

Mock AI and deployment providers can be used to test application workflows without requiring live paid model calls or production deployments.

---

# Current Scope and Limitations

NOVENTRA is designed as an early-stage founder validation and launch platform.

The following distinctions are important:

- AI assessments are decision-support tools, not guarantees of business success.
- Financial projections depend on assumptions supplied or generated during assessment.
- Persona Arena produces simulated feedback, not actual customer research.
- Simulated conversion outcomes are not real website conversion analytics.
- Domain availability is time-sensitive and depends on the configured provider.
- Public website availability depends on the selected deployment configuration.
- Improvement Studio uses a controlled evaluation and promotion process; passing its gate is not proof of actual market success.

The platform is intended to help founders test assumptions earlier, identify weaknesses, and make better-informed decisions.

---

# Product Vision

The long-term vision for NOVENTRA is to create a continuous business validation loop.

A founder should be able to:

1. Describe an idea.
2. Understand its risks and financial assumptions.
3. Establish an initial brand.
4. Launch a testable web presence.
5. Evaluate how different customer profiles perceive it.
6. Identify the most important barriers.
7. Improve the offering and messaging.
8. Evaluate proposed changes before accepting them.

NOVENTRA aims to make early-stage business experimentation more structured, accessible, and iterative.

---

## Project

**NOVENTRA — From Business Idea to Evidence-Driven Launch.**

Built to help founders move from assumptions to structured validation, and from validation to continuous improvement.

