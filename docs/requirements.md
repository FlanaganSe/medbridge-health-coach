# Health Ally

**Status:** Active
**Category:** AI Solution
**Role:** All Roles
**Language:** Python
**Email Thread:** [Gmail](https://mail.google.com/mail/u/2/#inbox/FMfcgzQfCDWVNCgxLcRcbWnVTbzjKMQZ)
**Technical Contact:** Yes

---

## Problem Statement

Healthcare providers prescribe home exercise programs (HEPs) to patients, but adherence is notoriously low — patients fall off their programs when they don't feel supported between visits. Clinicians are already stretched thin and don't have bandwidth for regular motivational check-ins with every patient.

We need an AI-powered accountability partner that proactively engages patients through onboarding, goal-setting, and follow-up, without crossing into clinical advice.

---

## Functional Requirements

### 1. Onboarding Conversation Flow

The coach initiates a multi-turn conversation that:
- Welcomes the patient
- References their assigned exercises
- Elicits an exercise goal (open-ended)
- Extracts a structured goal from the patient's response
- Confirms and stores the goal

**Edge cases to handle:** patient never responds, gives unrealistic goals, refuses to commit, or asks clinical questions mid-flow.

### 2. LangGraph Agent with Phase Routing

A main router graph that reads phase state and dispatches to phase-specific subgraphs.

**Phase lifecycle:** `PENDING` → `ONBOARDING` → `ACTIVE` → `RE_ENGAGING` → `DORMANT`

Phase transitions are deterministic (application code), not LLM-decided.

### 3. Safety Classifier and Clinical Boundary Enforcement

Every generated message passes through a safety check before delivery.

- **Clinical content** (symptoms, medication, diagnosis, treatment) triggers a hard redirect to the care team.
- **Mental health crisis signals** trigger an urgent clinician alert.
- Blocked messages retry once with an augmented prompt, then fall back to a safe generic message.

### 4. Scheduled Follow-Up

- Time-based check-ins at **Day 2, 5, and 7** referencing the patient's goal.
- The coach adjusts tone based on interaction type (celebration vs. nudge vs. check-in).

### 5. Disengagement Handling

- Exponential backoff on unanswered messages: **1 → 2 → 3 → dormant**.
- Clinician alert after 3 unanswered messages.
- Warm re-engagement when a dormant patient returns.

### 6. Tool Calling

The LLM autonomously calls tools:
- `set_goal`
- `set_reminder`
- `get_program_summary`
- `get_adherence_summary`
- `alert_clinician`

Tool implementations can be stubbed, but the interface and invocation logic must be real.

### 7. Consent Gate

No coach interaction occurs unless the patient has both:
1. Logged into MedBridge Go
2. Consented to outreach

Verified on **every interaction**, not just thread creation.
