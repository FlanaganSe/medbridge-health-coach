---
name: complete
description: Closes the current task lifecycle. Promotes decisions to ADR log, cleans up ephemeral plan files. Use after all milestones are done.
disable-model-invocation: true
---
Close out the current task: verify completion, preserve decisions, clean up plans.

## Process

### 1. Verify completion
Read `.claude/plans/plan.md`. Check that all milestone steps are marked `[x]`.
If any are incomplete, list them and ask whether to proceed anyway.

### 2. Promote decisions
Review what was built during this task. If any architectural decisions were made
(new patterns, technology choices, structural changes, important trade-offs),
draft an ADR entry:

```
### ADR-NNN: [Title]
**Date:** YYYY-MM-DD
**Status:** accepted
**Context:** [Why -- 1-2 sentences]
**Decision:** [What -- 1-2 sentences]
**Consequences:** [What follows]
```

Append to `docs/decisions.md` using the next sequential ADR number.
If no architectural decisions were made, skip this step.

### 3. Flag system doc updates
If the completed work changed the architecture, domain model, or constraints:
- Tell the user: "Consider running `/product-overview` to update system documentation."

### 4. Clean up ephemeral plans
Delete these files if they exist:
- `.claude/plans/prd.md`
- `.claude/plans/research.md`
- `.claude/plans/plan.md`

Preserve `.claude/plans/.gitkeep`.

### 5. Summary
Print:
- What was completed (from the plan title/summary)
- Any ADRs written (with ADR number and title)
- Files cleaned up

$ARGUMENTS
