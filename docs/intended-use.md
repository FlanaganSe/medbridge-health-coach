# Intended Use Statement

**Product:** MedBridge AI Health Coach
**Version:** MVP (v0.1.0)
**Date:** 2026-03-10
**Status:** Internal — controlled launch

## Purpose

The MedBridge AI Health Coach is an AI-powered accountability partner designed to proactively engage patients in their home exercise program (HEP) adherence through the MedBridge Go patient application.

## Intended Users

- **Primary:** Patients enrolled in a MedBridge home exercise program who have:
  - Logged into MedBridge Go
  - Consented to AI-powered coaching outreach
- **Secondary:** Clinicians who receive alerts when patients show signs of disengagement or crisis

## Intended Use

The Health Coach:
1. **Onboards** patients by establishing exercise-related goals through guided conversation
2. **Follows up** on Day 2, 5, and 7 post-onboarding to check exercise adherence
3. **Re-engages** patients who stop responding using a graduated backoff sequence
4. **Escalates** to clinicians when patients show signs of crisis or sustained disengagement

## Boundaries — What the Coach Does NOT Do

The Health Coach is explicitly NOT intended to:

1. **Provide clinical advice** — The coach must not offer guidance on symptoms, medication, diagnosis, treatment plans, or exercise modifications. All clinical questions are redirected to the care team.

2. **Replace clinical judgment** — The coach is an accountability partner, not a healthcare provider. It cannot assess clinical outcomes or make treatment decisions.

3. **Operate without consent** — No interaction occurs unless the patient has both logged into MedBridge Go AND consented to coaching outreach. Consent is verified on every interaction.

4. **Generate unreviewed content** — Every outbound message passes through a multi-layer safety pipeline (crisis check, safety classifier, retry/fallback) before delivery.

## Safety Architecture

| Layer | Purpose | Mechanism |
|---|---|---|
| Consent Gate | Prevent unauthorized outreach | Per-interaction consent verification via MedBridge Go API |
| Crisis Check | Detect patient distress | LLM classifier on inbound messages; explicit crisis triggers immediate clinician alert |
| Safety Classifier | Block unsafe outbound content | LLM classifier on outbound messages; clinical content blocked and redirected |
| Retry with Augmented Prompt | Recover from false positives | One retry with safety-emphasizing prompt modification |
| Deterministic Fallback | Last resort safe response | Hardcoded template messages that are pre-approved and never LLM-generated |
| Clinician Escalation | Human oversight | Durable alert intent for crisis, disengagement, and classifier failures |

## Risk Controls

- **Fail-safe classifier errors**: If the safety classifier fails (API error, timeout), the system blocks the message (false positive preferred over false negative)
- **Fail-safe consent errors**: If the consent service is unreachable, all patient messages are blocked (fail-closed)
- **Audit trail**: All safety decisions, consent checks, and delivery attempts are recorded in an append-only audit log
- **Phase transitions are deterministic**: Application code controls patient lifecycle phases — the LLM cannot trigger phase changes
- **Advisory locking**: Concurrent interactions for the same patient are serialized to prevent state corruption

## Limitations

1. The coach operates in English only (MVP)
2. Clinical boundary detection depends on LLM classification accuracy — false positives may block legitimate coaching messages
3. Crisis detection is supplementary, not a replacement for clinical crisis protocols
4. The system requires continuous connectivity to the LLM provider (Anthropic Claude)
5. Exercise adherence tracking relies on patient self-report, not objective measurement

## Compliance

- **HIPAA**: PHI is handled in accordance with BAA terms. See `docs/phi-data-flow.md` for data flow documentation.
- **Audit retention**: 6-year minimum for audit events per HIPAA requirements
- **No PHI in logs**: Enforced by `scrub_phi_fields` structlog processor and code-level prohibitions
- **Consent verification**: Per-interaction, not just at enrollment
