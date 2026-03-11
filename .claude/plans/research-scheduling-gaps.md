# Research: Scheduling and Backoff Gaps

**Date:** 2026-03-10
**Scope:** Four specific concerns about the scheduling model raised for review
**Sources read:**
- `research-scheduling-observability.md` (1012 lines)
- `research-domain-model.md` (1163 lines)
- `plan.md` (scheduling steps, M5 section)
- `prd.md` (FR-6, FR-7, AC-8, AC-9)
- `RESEARCH_INDEX.md` (correction table)

---

## 1. Current State

What the research documents say on each of the four concerns, with exact citations.

### 1.1 When does `unanswered_count` increment?

The domain model research does **not** explicitly state "increment on send" or "increment on no-response-detected." What it does specify:

- The field exists on `Patient` as a durable DB column (`research-domain-model.md:584`)
- A repository method `increment_unanswered_count()` exists (`research-domain-model.md:706-716`) — a raw SQL `UPDATE ... SET unanswered_count = unanswered_count + 1`
- `reset_unanswered_count()` exists for when the patient responds (`research-domain-model.md:718-724`)
- `unanswered_count` is listed in the "Unanswered count: Domain DB — Durable; drives backoff policy" row (`research-domain-model.md:850`)

The **plan.md** (the implementation plan derived from this research) fills the gap explicitly at line 945:

> `unanswered_count` is incremented by `save_patient_context` when a scheduler-initiated outreach completes (outbox entry created but no patient response). Reset to 0 when `invocation_source="patient"` (patient responded).

**Translation of "outbox entry created but no patient response":** The count is incremented during the NEXT scheduler-triggered invocation that finds no inbound patient message since the previous outreach — not at the moment the outreach message is written to the outbox. The scheduler fires a follow-up job. The job invokes the graph with `invocation_source="scheduler"`. The graph checks whether a patient reply has arrived (by inspecting conversation history for a new `HumanMessage` after the last outbound message). If none is found, the ACTIVE or RE_ENGAGING agent node records the miss, increments the count, and schedules the next backoff job.

**The concern is valid.** If `unanswered_count` were incremented at outbox-write time (when outreach is sent), every outreach message would increment the count regardless of whether the patient eventually responded. The correct trigger is at the point a follow-up invocation determines no reply came. This is what "outbox entry created but no patient response" means in plan.md:945 — the scheduler job executes, observes no patient reply since last outreach, increments, and either schedules the next backoff or transitions to DORMANT.

**What the research does NOT say:** The research does not define exactly how the graph detects "no patient message received since last outreach." The mechanism requires the graph to inspect `state["messages"]` for a `HumanMessage` with a timestamp after `patient.last_contact_at`. This is implementation detail not covered in either research file.

### 1.2 Pre-seeded Day 5 / 7 jobs surviving phase changes

The research documents **do not address job cancellation on phase transition**. Specifically:

- `research-scheduling-observability.md:379-388` describes the backoff sequence but only mentions cancelling pending backoff jobs when the patient responds:

> If the patient responds, cancel pending backoff jobs by setting their status to `completed` with a `cancelled_by_patient_response` note in the metadata.

- The research **does not mention** cancelling Day 5 or Day 7 jobs when a patient goes DORMANT or any other phase change.
- `prd.md:154` (FR-6) requires "Day 2 / 5 / 7 follow-up" but says nothing about when these jobs should be cancelled.
- The plan.md job type list (`plan.md:849`) lists `day_2`, `day_5`, `day_7`, and `backoff` handlers but gives no cancellation logic.

**The concern is valid and unresolved by the research.** The research does not specify whether Day 5/7 jobs are pre-seeded at onboarding completion or created one at a time. It does not specify what happens to pending follow-up jobs when a patient transitions to DORMANT.

### 1.3 The 72-hour onboarding timeout handler

The plan.md describes the `pending_node` creating a 72-hour timeout job at line 619:

> `pending_node` is NOT a no-op. It is the PENDING→ONBOARDING initiation point... creates a 72-hour onboarding timeout job...

And the reconciliation step at line 912:

> Periodic sweep (every 10 min): find patients in ACTIVE or ONBOARDING phase with no pending scheduled job → create missing follow-up jobs (ACTIVE) or missing timeout jobs (ONBOARDING, 72h). This ensures stuck ONBOARDING patients eventually transition to DORMANT via `no_response_timeout`.

The **domain model** includes `no_response_timeout` as a defined transition event (`research-domain-model.md:82`):

```python
(PatientPhase.ONBOARDING, "no_response_timeout"): PatientPhase.DORMANT,
```

However, neither research file defines a `dispatch_job` handler for a `job_type = "onboarding_timeout"`. The scheduling research names it as a job comment at line 66 (`'dormant_transition'` appears in comments) but the validation research file (`research-validation-graph-topology-and-scheduling.md:231`) explicitly flags this:

> **Gap 3: `dispatch_job` Implementation.** Not defined. The scheduling research calls `await dispatch_job(job)` at line 180 but leaves the implementation as "Domain-specific dispatch."

**The concern is valid.** The `no_response_timeout` transition exists in the state machine and the 72-hour job creation is documented, but no research file specifies the handler implementation.

### 1.4 Cancelling pending backoff jobs on patient response

The research **does** address this, at `research-scheduling-observability.md:388`:

> If the patient responds, cancel pending backoff jobs by setting their status to `completed` with a `cancelled_by_patient_response` note in the metadata.

So the cancellation mechanism is defined (set `status = "completed"` with metadata note). What is **not** defined:
- Which node detects the patient response and performs this cancellation
- Whether this happens in `save_patient_context` or in the reactive agent node
- Whether the cancellation is part of the same transaction as the `unanswered_count` reset

The plan.md at line 945 says the count resets when `invocation_source="patient"` but does not describe the job cancellation step.

---

## 2. Constraints

The following cannot change:

1. **Idempotency key uniqueness:** Each logical job must have a stable key so `INSERT ... ON CONFLICT DO NOTHING` prevents duplicates (`research-scheduling-observability.md:272-303`). Key format: `{patient_id}:{job_type}:{reference_date}` with attempt suffix for backoff.

2. **Phase transitions are application code:** The scheduler job handler calls `apply_phase_transition()` for `no_response_timeout`. The LLM cannot initiate this (`immutable.md:3`).

3. **Outbox INSERT in same transaction as domain state:** If a job handler writes both a phase transition and an outbox entry, they must be in the same `session.begin()` block (`RESEARCH_INDEX.md:68`).

4. **`unanswered_count` is a durable DB field.** It lives on `Patient`, not in LangGraph state. Graph state carries a copy for within-invocation use only.

5. **Backoff cancellation is `status = "completed"` with metadata note** (not a new status value): `research-scheduling-observability.md:388`. The `scheduled_jobs` status column values are: `pending | processing | completed | failed | dead`.

---

## 3. Options

### Question A: When to increment `unanswered_count`

**Option A1 (correct per plan.md:945): Increment at no-response detection, not at send**

The scheduler fires a follow-up job. The job invokes the graph with `invocation_source="scheduler"`. Inside the agent node, the graph checks `state["messages"]` for any `HumanMessage` after `last_contact_at`. None found → increment count, schedule next backoff job, transition phase if threshold reached.

Trade-offs:
- (+) Count accurately reflects unanswered messages, not sent messages
- (+) If patient responds between the outreach send and the follow-up job firing, count never increments
- (-) Requires the graph to detect "no reply since last_contact_at" — not trivially obvious from message history alone
- (-) `last_contact_at` must be updated atomically when each outreach is sent so the detection works correctly

**Option A2 (incorrect): Increment at outreach send**

Increment `unanswered_count` inside `save_patient_context` when an outbox entry for a proactive outreach message is written.

Trade-offs:
- (+) Simple — no detection logic needed
- (-) Wrong: patient who responds an hour after outreach would still have incremented count
- (-) Premature RE_ENGAGING / DORMANT transitions

**Recommendation: A1.** This is what plan.md:945 specifies. The detection mechanism (inspect messages for HumanMessage after `last_contact_at`) must be explicitly implemented.

### Question B: Pre-seeded Day 5/7 jobs vs. one-at-a-time scheduling

**Option B1: Pre-seed all three follow-up jobs at onboarding completion**

When the patient confirms their goal (`goal_confirmed` event, ONBOARDING → ACTIVE), create three scheduled jobs immediately: Day 2, Day 5, and Day 7 follow-ups. Idempotency keys use the enrollment date as `reference_date`.

Trade-offs:
- (+) Simple to reason about — one place creates all follow-up jobs
- (+) Reconciliation can detect missing jobs easily (patient in ACTIVE but fewer than 3 pending jobs)
- (-) Day 5 and Day 7 jobs survive phase transitions to DORMANT unless explicitly cancelled
- (-) If patient goes DORMANT on the Day 2 follow-up, Day 5 and Day 7 jobs still fire
- (-) The research only mentions cancelling backoff jobs on patient response; it says nothing about cancelling pre-seeded follow-up jobs on phase change

**Option B2: Create jobs one at a time (chain scheduling)**

Day 2 follow-up job is created at onboarding completion. When Day 2 handler executes:
- If patient responded: create Day 5 job
- If patient did not respond: transition to RE_ENGAGING and start backoff sequence (no Day 5 or Day 7)

Trade-offs:
- (+) No orphaned jobs to cancel — jobs only exist when the patient is in the right state to receive them
- (+) Phase-change cleanup is automatic (no pending jobs left behind)
- (-) Requires the Day 2 handler to decide whether to create Day 5 or switch to backoff
- (-) More complex job handler logic
- (-) Reconciliation is harder ("ACTIVE patient with Day 2 completed but no Day 5 pending" requires knowing Day 2 outcome)

**Option B3: Pre-seed only Day 2; create Day 5/7 conditionally**

Hybrid: at onboarding completion, create only the Day 2 job. Day 2 handler conditionally creates Day 5. Day 5 handler conditionally creates Day 7.

Trade-offs:
- (+) Only one pending job at a time — no orphan risk at phase transitions (at most one job to cancel)
- (+) Follow-up jobs naturally encode current patient state at creation time
- (+) Closest to the backoff model the research does specify (each step creates the next)
- (-) Slightly more complex than pre-seeding; reconciliation must detect "ACTIVE, Day 2 completed, no Day 5 pending"

**Recommendation: B3.** The chain-scheduling model aligns with how the research defines backoff (`research-scheduling-observability.md:382-388` — each attempt creates the next). It limits orphaned jobs to at most one at a time. Phase transitions to DORMANT cancel at most one pending follow-up job rather than two.

### Question C: Onboarding timeout handler

**Option C1: Dedicated `job_type = "onboarding_timeout"` handler**

The 72-hour job is created by `pending_node`. The scheduler dispatches it to a handler `handle_onboarding_timeout(job)` which:
1. Checks patient's current phase. If not ONBOARDING (patient already advanced), mark job completed with metadata `{"skipped": "phase_already_advanced"}`.
2. If still ONBOARDING, call `apply_phase_transition(patient_id, "no_response_timeout", actor="scheduler")`.
3. Write an outbox entry for clinician notification if required.
4. Mark job completed.

Trade-offs:
- (+) Explicit and testable
- (+) Phase check before transition prevents double-firing if reconciliation creates a duplicate timeout job
- (+) Same pattern as all other job handlers
- (-) One more handler to implement

**Option C2: Reuse the existing backoff handler with a different `job_type`**

Stretch the backoff handler to cover onboarding timeout as a special case.

Trade-offs:
- (-) Different semantic (ONBOARDING → DORMANT via timeout vs. ACTIVE → RE_ENGAGING via unanswered) — mixing them adds cognitive complexity
- (-) Harder to test independently

**Recommendation: C1.** The handler is ~20 lines of straightforward code and follows the exact same pattern as every other job type handler.

### Question D: Backoff job cancellation on patient response

**Option D1: Cancel in `save_patient_context` when `invocation_source="patient"`**

When the graph's reactive path completes (patient message processed), `save_patient_context` queries pending backoff jobs for this patient and sets `status = "completed"` with metadata `{"cancelled_by": "patient_response"}`. Done in the same `session.begin()` block.

Trade-offs:
- (+) Centralized — one place handles cancellation
- (+) Atomic with the count reset and the `patient_responded` phase transition
- (-) `save_patient_context` becomes responsible for querying the jobs table (it currently only writes)

**Option D2: Cancel in the RE_ENGAGING agent node**

The RE_ENGAGING node detects `invocation_source="patient"`, adds a cancellation request to `pending_effects`, which `save_patient_context` then flushes.

Trade-offs:
- (+) The node that manages the backoff sequence (RE_ENGAGING) also cancels its own jobs — high cohesion
- (+) `save_patient_context` remains a flush-only node with no query logic
- (-) Cancellation logic depends on `pending_effects` structure being correctly populated

**Recommendation: D2.** Keeping `save_patient_context` as a flush-only writer is the right architectural boundary. RE_ENGAGING node owns backoff state; it should own cancellation. The `pending_effects` dict already supports an `"cancelled_jobs"` key pattern.

---

## 4. Recommendation

### 4.1 `unanswered_count` timing

Increment **at no-response detection**, not at send. The correct call site is inside the scheduler-triggered agent node (ACTIVE or RE_ENGAGING) after inspecting `state["messages"]` for a patient reply since `last_contact_at`. The `last_contact_at` field on `Patient` must be updated atomically with the outbox write when outreach is sent, so the detection has a correct anchor.

**Implementation gap to fill:** Add `last_contact_at` update to `save_patient_context` whenever an outbound outreach message is added to `pending_effects.outbox_entries`. This is not explicitly defined in any research file.

### 4.2 Day 5/7 job seeding strategy

Use **chain scheduling (Option B3)**:
- Onboarding completion (`goal_confirmed`) creates only the Day 2 job.
- Day 2 handler runs. If patient responded: creates Day 5. If not: transitions to RE_ENGAGING, creates first backoff job.
- Day 5 handler runs. If patient responded: creates Day 7. If not: increments unanswered count, creates backoff job.
- Day 7 handler runs as final check-in.

This eliminates orphaned pre-seeded jobs entirely. Phase transitions to DORMANT can cancel at most one pending follow-up job.

**The research does not explicitly say Day 5 and Day 7 should be pre-seeded.** PRD FR-6 (`prd.md:154`) requires "Day 2 / 5 / 7 follow-up" but says nothing about when they are created. Chain scheduling satisfies FR-6 while avoiding the orphan problem.

### 4.3 Onboarding timeout handler

Implement a dedicated `handle_onboarding_timeout` handler (Option C1):
- Guard with phase check: if `patient.phase != ONBOARDING`, mark completed with skip metadata and return.
- Call `apply_phase_transition(patient_id, "no_response_timeout", actor="scheduler")`.
- Idempotency: the phase guard provides replay safety.

### 4.4 Backoff job cancellation

Cancel pending backoff jobs in the **RE_ENGAGING agent node** (Option D2) when `invocation_source="patient"`:
- Node adds `pending_effects["cancelled_jobs"] = [list of backoff job idempotency keys or IDs for this patient]`.
- `save_patient_context` executes `UPDATE scheduled_jobs SET status='completed', metadata=jsonb_set(metadata, '{cancelled_by}', '"patient_response"') WHERE id = ANY(:ids) AND status = 'pending'` inside the same transaction.
- Same transaction also resets `unanswered_count` to 0.

---

## 5. Gaps Still Open After This Research

These are not answered by any existing research document and require a decision before M5 implementation:

1. **How does the graph detect "no reply since last outreach"?** The message inspection logic (checking `state["messages"]` for HumanMessage after `last_contact_at`) needs an explicit implementation pattern. Should it check by timestamp on the message objects or by position in the message list?

2. **Are Day 5 and Day 7 always "follow-up" jobs or does the job type carry the day number?** The idempotency key scheme (`{patient_id}:day_5_followup:{date}`) suggests distinct job types — confirm this in the job handler dispatch table.

3. **What happens to a pre-existing Day 2 job if reconciliation runs before it fires?** The reconciliation sweep at plan.md:912 creates missing follow-up jobs for ACTIVE patients with no pending jobs. Under chain scheduling, an ACTIVE patient may legitimately have no Day 5 job yet (Day 2 hasn't fired). The reconciliation logic needs to check job completion history, not just pending count.

4. **Backoff cancellation query scope:** When cancelling backoff jobs for a patient, query by `patient_id + status='pending' + job_type IN ('backoff_check', 'dormant_transition')` or by `patient_id + status='pending'` broadly? The narrow query is safer (avoids accidentally cancelling future Day 7 job under pre-seed model; irrelevant under chain scheduling).
