# PRD: MedBridge AI Health Coach — MVP

**Status:** Draft v1.6
**Date:** 2026-03-10
**Primary Inputs:** [`docs/requirements.md`](../../docs/requirements.md), [`docs/prd.md`](../../docs/prd.md), [`docs/decisions.md`](../../docs/decisions.md), [`FINAL_CONSOLIDATED_RESEARCH.md`](./FINAL_CONSOLIDATED_RESEARCH.md)  
**Validation:** This revision was informed by a fresh verification pass against official framework, cloud, and vendor documentation on 2026-03-10.  
**Working contract:** This is the working PRD used to derive implementation plans. Any accepted change here that affects scope, safety, stack direction, or milestone gates must be mirrored into `docs/prd.md` and ADRs before implementation starts.

## 1. Document Contract

This PRD is the MVP product contract for the MedBridge Health Coach. It exists to make the project buildable without forcing premature implementation detail into every milestone.

This document separates three things on purpose:

- **Locked requirements:** must remain true throughout planning and implementation.
- **Preferred implementation direction:** the current lowest-risk recommendation from research.
- **Open questions and ADR triggers:** unresolved items that need an explicit decision before release-critical work proceeds.

Implementation detail that is useful but likely to change belongs in ADRs, plans, or technical designs, not in the core product contract.

## 2. Context and Problem

Healthcare providers prescribe home exercise programs (HEPs), but patient adherence drops when patients do not feel supported between visits. Clinicians do not have the capacity to provide consistent motivational follow-up to every patient.

The MVP is a backend-first AI accountability partner that proactively engages patients through onboarding, goal-setting, and scheduled follow-up while staying strictly outside clinical advice. The patient experience remains in MedBridge Go; this service is the workflow and orchestration engine behind it.

## 3. MVP Outcome

Build a safe, auditable, low-risk v1 that can:

- onboard patients into a non-clinical coaching relationship,
- capture and store a structured exercise goal,
- deliver deterministic follow-up and re-engagement flows,
- escalate when safety or disengagement rules require it,
- preserve future flexibility in providers, delivery channels, and deployment target without forcing a rewrite.

### 3.1 Release Readiness Outcomes

The MVP is release-ready when the team can demonstrate all of the following:

- no outbound coach message is sent without same-invocation consent verification,
- no outbound coach message bypasses the safety gate,
- phase transitions remain deterministic and application-owned,
- scheduled follow-up, backoff, and dormant transitions behave correctly and idempotently,
- clinician escalation triggers on crisis and three-unanswered-message paths,
- CI is green for lint, format check, type check, tests, and container build,
- launch-critical compliance artifacts and vendor approvals are complete before any real PHI is processed.

## 4. Scope

### 4.1 In Scope for MVP

- Proactive onboarding conversation initiated by the coach
- Open-ended goal elicitation, structured goal extraction, confirmation, and persistence
- Deterministic patient phase lifecycle: `PENDING -> ONBOARDING -> ACTIVE -> RE_ENGAGING -> DORMANT`
- Scheduled Day 2 / 5 / 7 follow-up
- Exponential backoff for unanswered outreach with clinician alert after the third miss
- Warm re-engagement when a dormant patient returns
- Safety classifier, clinical redirection, crisis escalation, and safe fallback behavior
- Real tool interfaces for `set_goal`, `set_reminder`, `get_program_summary`, `get_adherence_summary`, and `alert_clinician`
- Consent verification on every interaction
- Durable persistence, audit logging, observability baseline, and deployable CI/CD foundation

### 4.2 Explicit Non-Goals for MVP

- Standalone patient-facing web application
- Clinician dashboard beyond the minimum internal/operator hooks needed to support delivery
- General medical Q&A, diagnosis, treatment, or symptom coaching
- Autonomous multi-agent orchestration, vector retrieval, Redis, Kafka, Celery, or Temporal
- Provider-owned conversation state as the source of truth
- Production use of OpenRouter for PHI paths
- Multilingual support
- Production dependence on hosted tracing/eval platforms for PHI

## 5. Safety, Compliance, and Product Boundaries

### 5.1 Immutable Rules

These are non-negotiable:

1. Never generate clinical advice.
2. Verify consent on every interaction.
3. Keep phase transitions deterministic and outside the LLM.

### 5.2 PHI and Provider Guardrails

These constraints are part of the product definition, not a later hardening pass:

| Guardrail | Requirement |
| --- | --- |
| BAA chain | No real PHI flows until every enabled PHI processor is contractually covered. |
| Synthetic-only pre-launch | Non-production environments and pre-approval workflows use synthetic patient data only. |
| Provider feature eligibility | PHI paths may use only provider features explicitly covered by the current BAA / HIPAA-ready / zero-retention terms. Until separately re-approved, disable provider-managed conversation state, hosted retrieval/file tools, remote MCP, web search, batch/background modes that break zero-retention guarantees, prompt caching on PHI paths, code-execution tools, and beta/chat/workbench-style product surfaces. |
| App-owned state | Canonical patient state, goals, alerts, and auditability remain application-owned, not vendor-owned. |
| PHI minimization | LLM calls receive only the minimum context required for the coaching task. Logs, traces, and schemas must avoid patient-identifying content. |
| Compliance artifacts | `docs/phi-data-flow.md` and an internal intended-use statement are required before real PHI enters the system. |
| Launch geography | State-specific privacy and AI laws must be confirmed before serving patients in affected geographies. |

### 5.3 Clinical Boundary and Safety Pipeline

Every outbound coach message must pass through a layered safety flow:

1. Consent and eligibility gate before any LLM activity
2. Input-side crisis pre-check on patient messages
3. Main generation call with tightly scoped tools and prompts
4. Output-side safety classifier before delivery
5. One retry for blocked outbound coach copy before fallback, except where the path is already escalated as crisis handling
6. Deterministic safe fallback if still blocked
7. Clinician escalation for crisis or required intervention paths

The coach must redirect symptoms, diagnosis, medication, treatment, and similar clinical content to the care team. It must not answer clinically even when the patient asks directly.

### 5.4 Crisis Protocol

Explicit or high-confidence crisis signals bypass normal coaching flow:

- create a durable urgent clinician-alert intent immediately,
- deliver a safe patient-facing response with 988 Lifeline guidance,
- avoid counseling or “talking through” the crisis,
- preserve alert delivery through retries and operator visibility if transport is unavailable.

### 5.5 Consent Verification

Every interaction must verify that the patient is both:

1. logged into MedBridge Go, and
2. currently consented to outreach.

Consent is checked per interaction, not per thread, and failure must fail safe before any LLM call or outbound delivery attempt.

### 5.6 Auditability

The service must maintain append-only auditability for at least the following event classes:

- consent checks,
- safety decisions,
- outbound delivery attempts,
- phase transitions,
- clinician alerts,
- tool invocations,
- scheduling and job execution lifecycle.

The implementation must support PHI-safe metadata and queryable event history. Raw message content is not required in audit records and should not be the default.

## 6. Functional Requirements

| ID | Requirement | Notes |
| --- | --- | --- |
| FR-1 | The system must initiate a multi-turn onboarding conversation that welcomes the patient, references assigned exercises, elicits a goal, confirms the goal, and stores it. | Must handle no response, refusal, unrealistic goals, and clinical questions during onboarding. |
| FR-2 | The system must route every interaction through a deterministic phase-aware workflow. | MVP uses a single LangGraph with deterministic routing; this intentionally resolves the older `subgraphs` wording in `docs/requirements.md` per ADR-001. |
| FR-3 | The system must support the full phase lifecycle `PENDING -> ONBOARDING -> ACTIVE -> RE_ENGAGING -> DORMANT`. | Phase changes are application rules, not model outputs. |
| FR-4 | Every outbound coach message must pass a safety and clinical-boundary check before delivery. | Blocked messages retry once with augmented prompting, then fall back to a safe template unless the message is already routed to the crisis path. |
| FR-5 | The system must detect crisis signals and trigger urgent clinician escalation. | Crisis behavior bypasses normal coaching flow. |
| FR-6 | The system must schedule follow-up outreach for Day 2, Day 5, and Day 7 using the patient goal and current context. | Scheduling must honor timezone and quiet-hours rules, and outbound tone must adapt across celebration, nudge, and check-in contexts. |
| FR-7 | The system must apply exponential backoff to unanswered outreach and transition to dormant state after the third unanswered coach-initiated message. | Third unanswered path must generate clinician alert. |
| FR-8 | The system must support warm re-engagement when a dormant patient returns. | Re-engagement must differ from onboarding and routine follow-up. |
| FR-9 | The LLM must be able to request real tool interfaces for `set_goal`, `set_reminder`, `get_program_summary`, `get_adherence_summary`, and `alert_clinician`. | Tool implementations may be stubbed initially, but invocation contracts must be real and testable. |
| FR-10 | The system must verify MedBridge Go login and outreach consent on every interaction before any LLM call or outbound message attempt. | Consent failure must fail safe and emit an auditable event. |

## 7. Non-Functional Requirements

| ID | Requirement | Verification intent |
| --- | --- | --- |
| NFR-1 | The service must be maintainable through strict typing, explicit boundaries, and small replaceable modules. | Pyright strict, constructor-injected integrations, isolated domain tests. |
| NFR-2 | The service must keep application state durable and queryable across sessions. | PostgreSQL in production, SQLite locally, LangGraph checkpointing, relational domain records. |
| NFR-3 | The service must be safe by default and fail safe when dependencies are unavailable. | Consent failures block outreach; safety violations fall back safely; alerts remain durable. |
| NFR-4 | The service must provide append-only auditability for consent checks, safety decisions, phase transitions, scheduling, tool invocation, and delivery outcomes. | Audit events persisted and queryable. |
| NFR-5 | The service must be idempotent across inbound events, scheduled jobs, tool invocations, and message delivery. | Duplicate delivery and duplicate escalation are prevented by design. |
| NFR-6 | The service must be deployable through GitHub Actions with reproducible checks and container build steps. CI tests must not depend on external LLM API availability. | CI must run lint, format check, type check, tests (using deterministic fakes, not live LLM calls), and image build. |
| NFR-7 | The MVP architecture must preserve future expansion without forcing a rewrite. | Provider abstraction, delivery-channel abstraction, scheduler abstraction, tenant-ready schema. |
| NFR-8 | Non-production environments must avoid real PHI. | Synthetic-only policy until compliance gate is satisfied. |

## 8. Architecture and Stack Decisions

### 8.1 Locked for MVP

These choices materially reduce orchestration risk and should be treated as locked:

| Area | Locked direction | Why it is locked |
| --- | --- | --- |
| Runtime and language | Python 3.12+ | Project stack rule |
| API framework | FastAPI | Async-native, mature, Pydantic v2-compatible, straightforward lifespan hooks |
| AI orchestration | LangGraph 1.x | Best fit for durable, phase-driven workflow orchestration |
| Workflow shape | Single graph with deterministic routing | Lower complexity than subgraphs while preserving phase-specific behavior |
| Persistence | PostgreSQL in production, SQLite in local dev | Matches project constraints and lowest-risk durability model |
| Data access | SQLAlchemy 2.0 async + Pydantic v2 + Alembic + psycopg3 | Lowest-risk typed Python stack for this domain |
| Thread persistence | LangGraph checkpointer | Required for resumable thread-scoped state |
| Background work shape | PostgreSQL-backed scheduled-jobs / worker pattern behind a `SchedulerService` abstraction | Keeps scheduling queryable, HIPAA-friendly, and infrastructure-light while aligning with the canonical MVP delivery shape |
| Delivery reliability | Outbox pattern for outbound messaging and alerts | Supports retries, auditability, and crash recovery |
| Observability baseline | structlog + OTEL + audit DB | PHI-safer baseline than hosted tracing dependencies |
| Type checking | pyright (strict) | Best fit for TypedDict-heavy LangGraph and modern Python typing |
| Tests | pytest + pytest-asyncio | Async-first, ecosystem fit |
| CI/CD | GitHub Actions | Mandatory automation entry point for verification and builds |

### 8.2 Preferred but Reopenable

These are the current best directions, but they are not irreversible:

| Area | Preferred direction | Revisit trigger |
| --- | --- | --- |
| Primary LLM | Anthropic first-party API via an app-owned `ModelGateway`; current preferred family is Claude Sonnet 4.5 | Material change in evals, provider policy, cost, or compliance posture |
| Secondary / fallback LLM | OpenAI direct API via the same `ModelGateway`; exact approved fallback is chosen by eval gate at implementation time rather than hard-coded in prompts or plans | Model lineup or eval results change materially |
| Safety classifier | Claude Haiku 4.5 primary; evaluate a low-cost OpenAI candidate as the secondary comparison point before production lock | Cost / quality / approval constraints change materially |
| Background queue implementation | Default to a thin in-house `scheduled_jobs` polling worker; revisit Procrastinate only if custom scheduling code starts to dominate implementation complexity | If the custom worker becomes harder to reason about or maintain than a focused dependency |
| Cloud target | If MedBridge has no existing cloud standard, prefer GCP Cloud Run + Cloud SQL as the default starting point; if the org is already AWS-standardized, ECS/Fargate + RDS is equally valid | Organization platform standard or compliance constraints differ |
| LangGraph Store | Keep optional. Introduce only if cross-thread memory emerges that is not better modeled in relational application state | Cross-thread memory needs exceed the relational model cleanly |
| LLM tracing | OTEL + structlog first; consider self-hosted Phoenix OSS later if metadata-only LLM tracing becomes necessary | Production debugging needs exceed the baseline |
| Tenancy posture | Single-tenant launch with tenant-ready schema | Day-one customer scope requires stronger isolation |

### 8.3 Key Architecture Principles

- The product is a regulated workflow system, not a chatbot shell.
- Application-owned state is the source of truth; vendor-managed conversation state is not.
- Deterministic policy lives in plain Python domain logic with tests.
- The LLM is used for bounded generation, extraction, and tool selection, not policy ownership.
- Pydantic remains the default for API, settings, validation, and serialization; `TypedDict` is the explicit exception for LangGraph state because that is the current LangGraph v1-compatible best practice.
- LangGraph checkpointing is required for thread-scoped state.
- The relational domain database is the source of truth for patient state, goals, alerts, scheduling, and auditability.
- Avoid Store + domain-DB dual-write by default. If a LangGraph Store is later introduced, ownership boundaries must remain explicit and the domain DB still governs regulated product state.
- Model, notification, and alert integrations must sit behind app-owned interfaces (`ModelGateway`, `NotificationChannel`, `AlertChannel`, `SchedulerService`).

## 9. Orchestration Rules

### 9.1 LangGraph

- Use LangGraph for deterministic routing and durable execution, not as a policy engine.
- Keep all phase transitions in application code.
- Treat every side effect as replay-sensitive; nodes and external effects must be idempotent.
- Use a stable `thread_id` for resumable thread state.
- Use `Command` / conditional routing and `Runtime[Context]` with `context_schema` for modern typed graph construction.
- Do not use `interrupt()` for the safety gate; interrupts are for human approval workflows, not classifier-based delivery control.
- Do not build the MVP around deprecated prebuilt agent factories.

### 9.2 Async Data Access

- Use `expire_on_commit=False` for async SQLAlchemy sessions.
- Use one `AsyncSession` per concurrent task; do not share sessions across worker tasks.
- Avoid implicit lazy loads in async ORM paths; prefer explicit eager loading and repository boundaries.
- If LangGraph uses a PostgreSQL-backed saver while the application also uses SQLAlchemy, manage their connection lifecycles explicitly rather than assuming a single shared pool abstraction.

### 9.3 Reliability and Idempotency

- Dedupe inbound events on a stable source event key.
- Assign a stable job identity to each logical scheduled follow-up attempt.
- Assign a stable delivery key to each outbound patient message or clinician alert.
- Assign a stable tool call key for side-effecting tools.
- Pass provider-side idempotency keys where supported; otherwise suppress duplicates locally.
- Keep quiet hours, timezone handling, cadence, and backoff values configuration-driven rather than prompt-owned.

### 9.4 Health, Readiness, and Worker Topology

- Provide separate liveness and readiness endpoints.
- Liveness must not depend on a live model-provider call.
- Readiness must cover database connectivity, schema compatibility, and required internal worker dependencies.
- The deployment shape must support always-on background processing or an equivalent durable worker path. Do not assume a pure HTTP scale-to-zero design for follow-ups and outbox processing.
- API and worker processes must be deployable separately even if local development runs them together.

## 10. Acceptance Criteria

The MVP is done when all criteria below are true:

1. Given an interaction without valid login or outreach consent, the workflow exits before any LLM call or outbound delivery attempt and records a consent failure event.
2. Given onboarding input that contains clinical content, the coach does not answer clinically and routes to the approved safety response path.
3. Given crisis language, the system records the safety decision and triggers urgent clinician escalation with a durable alert intent.
4. Given a valid onboarding exchange, the system stores a structured goal and confirms it to the patient.
5. Given a patient in each supported phase, deterministic routing sends execution through the correct phase-specific path without model involvement.
6. Given a candidate outbound coach message, the system records a safety decision before any delivery attempt.
7. Given a blocked outbound message, the system retries once with augmented prompting and falls back to a safe deterministic template if still blocked, except when the message is already on the crisis path.
8. Given onboarding completion, the system schedules Day 2 / 5 / 7 follow-up while honoring timezone, quiet-hours, and tone-adaptation rules.
9. Given unanswered outreach, the system schedules the next contact using the configured backoff pattern and transitions to dormant on the third unanswered coach-initiated message.
10. Given a dormant patient response, the system uses the re-engagement path rather than replaying onboarding.
11. Given duplicate inbound events or duplicate job pickups, the system does not produce duplicate sends or duplicate alerts.
12. Given a service restart during pending follow-up work, background processing can recover without losing due jobs.
13. Application logs and traces used in non-dev operations do not contain disallowed PHI fields.
14. GitHub Actions passes lint, format check, type check, tests, and container build on pull requests.
15. Non-production workflows run without real PHI.

## 11. Delivery Strategy and Milestones

Milestones are behavioral slices, not file-by-file implementation checklists. Each milestone must be independently testable and must reduce downstream architectural risk.

| Milestone | Objective | Key deliverables | Exit gate |
| --- | --- | --- | --- |
| M1 Foundation and quality gate | Establish the clean project skeleton and verification baseline | FastAPI app shell, settings, health endpoints, typed config, logging baseline, Docker build, CI | Repo is reproducible in CI and boots locally without feature code |
| M2 Deterministic domain core | Lock down rules that must never drift into prompts | Phase model, consent contract, safety policy definitions, audit contract, idempotency primitives | Core policy logic is application-owned and independently testable |
| M3 Graph orchestration shell | Prove the workflow shape before adding live integrations | LangGraph state, deterministic router, phase nodes, fake model/tool wiring, checkpointed thread flow | Graph shape is stable and testable without real external services |
| M4 Safe onboarding | Deliver the first end-to-end patient value path safely | Onboarding flow, goal extraction, tool loop, safety gate, retry/fallback behavior | A patient can complete onboarding safely with auditable outcomes |
| M5 Durable follow-up and lifecycle management | Add multi-day persistence, scheduling, disengagement, and re-engagement | Scheduler/worker path, Day 2 / 5 / 7 cadence, quiet-hours logic, backoff, dormant transition, alert intent flow | Follow-up and lifecycle behavior are durable, idempotent, and restart-safe |
| M6 External integration and delivery | Connect the workflow engine to real system boundaries and make the result demonstrable | MedBridge Go adapters, notification and alert channels, outbox delivery, provider abstraction, read-only state query endpoints (phase, goals, safety decisions, alerts), internal demo chat UI (`demo-ui/`, React + Vite, consuming the backend's chat and state query APIs, served in dev/staging only) with a live observability sidebar showing current phase, extracted goals, safety decisions, and clinician alerts alongside the chat | Team can demo the full coaching lifecycle end to end with synthetic data through the internal chat UI, including real-time visibility into the workflow engine state |
| M7 Release hardening | Close the operational and compliance gaps required for controlled launch | Compliance artifacts, eval baseline, PHI-safe logging proof, deployment workflow, release runbook | Team can deploy safely once external approvals close |

## 12. Open Questions and ADR Triggers

| Topic | Why it matters | Resolve by | Resolution artifact |
| --- | --- | --- | --- |
| MedBridge Go integration contract | Consent verification, patient event flow, and auth assumptions depend on it | Before M3 exit | Interface spec |
| Clinician alert channel | Crisis and disengagement workflows are incomplete until the destination is known | Before M6 exit | ADR or integration spec |
| Cloud target | Deployment workflow and runtime topology depend on it | Before deploy work starts | ADR |
| Enabled PHI-path vendor approvals | Production PHI is blocked without it | Before any real PHI | Compliance sign-off |
| Launch tenancy scope | Affects schema defaults and auth boundaries | Before persistent production schema lock | ADR |
| Patient timezone source | Impacts scheduling behavior and fallback logic | Before M5 exit | Integration / product decision |
| Retention and deletion policy | Checkpoint, conversation, and audit retention cannot be guessed | Before production PHI | Operational policy |
| Launch geography | State-specific privacy and AI obligations may affect rollout | Before pilot launch | Legal / compliance review |
| Approved provider configuration | Exact production models and snapshots must be eval-gated, not prompt-hardcoded | Before production lock | Eval record + config decision |

## 13. Top Risks to Manage Explicitly

| Risk | Why it matters | Mitigation direction |
| --- | --- | --- |
| Clinical-boundary failure | Highest safety and liability risk | Layered safety, adversarial regression suite, deterministic fallback |
| Consent path drift | Can create unauthorized outreach | Same-invocation verification, fail-safe behavior, audit enforcement |
| State divergence between graph state and application records | Can corrupt workflow behavior and audits | Keep the domain DB as source of truth and avoid unnecessary dual-write paths |
| Scheduler or delivery duplication | Leads to duplicate nudges or alerts | Stable job identities, outbox pattern, idempotency tests, recovery tests |
| Provider behavior drift | Can silently change safety or tool behavior | Regression evals, provider abstraction, pinned approved configurations |
| Premature infrastructure complexity | Slows MVP and obscures failure modes | Keep v1 PostgreSQL-backed and abstraction-driven |
| External integration blockers | Can stall implementation late | Resolve open questions with explicit artifacts and milestone gates |
| Compliance assumption errors | Can invalidate launch readiness late in the cycle | Keep launch-critical compliance items explicit in milestone gates |

## 14. Source Basis

This PRD is based on:

- the locked product requirements in `docs/requirements.md`,
- the current canonical product framing in `docs/prd.md`,
- ADR-001 in `docs/decisions.md`,
- consolidated internal research in `FINAL_CONSOLIDATED_RESEARCH.md`,
- and a fresh external verification pass against official documentation for LangGraph, FastAPI, SQLAlchemy, psycopg, Procrastinate, OpenAI, Anthropic, Google Cloud, and AWS on 2026-03-10.

This document is the input to implementation planning. Exact package pins belong in `pyproject.toml` and lockfiles, not here.
