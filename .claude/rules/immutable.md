---
description: Non-negotiable project rules. Violations must be flagged immediately.
---
# Immutable Rules

1. **Never generate clinical advice** — the coach must redirect all clinical content (symptoms, medication, diagnosis, treatment) to the care team. This is a safety and liability boundary.
2. **Verify consent on every interaction** — no coach interaction unless patient has logged into MedBridge Go AND consented to outreach. Checked per-interaction, not just at thread creation.
3. **Phase transitions are deterministic** — application code controls phase transitions (`PENDING` → `ONBOARDING` → `ACTIVE` → `RE_ENGAGING` → `DORMANT`), never the LLM.

<!-- Add new invariants as discovered, with one-line justification. -->
