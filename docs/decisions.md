# Decisions

Append-only log. Never edit past entries.

## Format
```
### ADR-NNN: [Title]
**Date:** YYYY-MM-DD
**Status:** accepted | superseded by ADR-NNN
**Context:** [Why — 1-2 sentences]
**Decision:** [What — 1-2 sentences]
**Consequences:** [What follows]
```

---

<!-- Add new decisions below this line -->

### ADR-001: Single StateGraph with Conditional Edges Instead of Subgraphs
**Date:** 2026-03-10
**Status:** accepted
**Context:** `docs/requirements.md` §2 references "phase-specific subgraphs." With 5 phases, subgraphs add complexity (schema alignment, cross-graph debugging, checkpoint namespace management) without sufficient benefit. Different tools, prompts, and LLM bindings per phase are achievable via per-node configuration in a flat graph. Research §4.2 confirms single StateGraph is recommended at this scale.
**Decision:** Use a single `StateGraph` with `add_conditional_edges` for phase routing. No subgraphs. Annotate `PatientState` fields with phase-ownership comments to make future extraction mechanical.
**Consequences:** Simpler debugging and state management. Revisit subgraph extraction if: (1) HITL interrupts are required inside a phase, (2) more than 3 genuinely phase-private state fields accumulate in `PatientState` for a single phase, or (3) phases require independent development by separate teams. Phase count alone is not the trigger. **Migration cost:** subgraph extraction changes the LangGraph checkpoint namespace scheme (`""` → `"phase:UUID"`). In-flight patient threads must be drained or their checkpoint rows migrated — checkpoint blobs contain PHI, so post-production migration is a HIPAA change-management event. The practical migration window with minimal cost is before M4 (when persistent per-patient threads begin).
