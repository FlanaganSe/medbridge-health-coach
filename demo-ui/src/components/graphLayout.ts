/**
 * Static layout data for the Health Ally LangGraph architecture diagram.
 *
 * Node IDs match NODE_LABELS in useSSE.ts exactly.
 * Topology derived from docs/architecture.dot.
 * Consumed by GraphView (M7) for SVG rendering.
 */

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface GraphNode {
  /** Must match keys in NODE_LABELS (useSSE.ts) */
  id: string;
  /** Display label */
  label: string;
  /** Center x in SVG coordinate space */
  cx: number;
  /** Center y in SVG coordinate space */
  cy: number;
  /** Cluster membership */
  cluster: "ingestion" | "phases" | "tools" | "safety" | "persistence";
  /** Rendering shape hint */
  shape: "rect" | "diamond" | "rounded" | "hexagon";
}

export interface GraphEdge {
  /** Source node id (or "__START__" / "__END__") */
  from: string;
  /** Target node id (or "__START__" / "__END__") */
  to: string;
  /** Optional edge label */
  label?: string;
  /** Line style */
  style: "solid" | "dashed";
  /** Color category */
  color: "default" | "danger" | "warning" | "success" | "phase";
  /** Back-edge that routes upward (needs bezier in M7) */
  isBackEdge?: boolean;
}

export interface ClusterDef {
  id: string;
  label: string;
  bgColor: string;
  borderColor: string;
}

// ---------------------------------------------------------------------------
// SVG ViewBox
// ---------------------------------------------------------------------------

export const VIEWBOX = "0 0 680 520";

// ---------------------------------------------------------------------------
// Nodes — 14 pipeline nodes in 6 visual layers
// ---------------------------------------------------------------------------
// Layer 1 (y=60):   consent_gate
// Layer 2 (y=130):  load_patient_context, crisis_check, manage_history
// Layer 3 (y=220):  pending_node, onboarding_agent, active_agent, reengagement_agent, dormant_node
// Layer 4 (y=310):  tool_node
// Layer 5 (y=390):  safety_gate, retry_generation, fallback_response
// Layer 6 (y=460):  save_patient_context
// START (y=20) and END (y=510) are rendered as special SVG elements, not in this array.

export const GRAPH_NODES: GraphNode[] = [
  // Layer 1 — Ingestion entry
  { id: "consent_gate",         label: "Consent",       cx: 340, cy: 60,  cluster: "ingestion",   shape: "diamond" },

  // Layer 2 — Ingestion pipeline
  { id: "load_patient_context", label: "Context",       cx: 196, cy: 130, cluster: "ingestion",   shape: "rect" },
  { id: "crisis_check",         label: "Crisis Check",  cx: 340, cy: 130, cluster: "ingestion",   shape: "diamond" },
  { id: "manage_history",       label: "History",       cx: 484, cy: 130, cluster: "ingestion",   shape: "rect" },

  // Layer 3 — Phase agents (widest row)
  { id: "pending_node",         label: "Pending",       cx: 68,  cy: 220, cluster: "phases",      shape: "rounded" },
  { id: "onboarding_agent",     label: "Onboarding",    cx: 196, cy: 220, cluster: "phases",      shape: "rect" },
  { id: "active_agent",         label: "Active Coach",  cx: 340, cy: 220, cluster: "phases",      shape: "rect" },
  { id: "reengagement_agent",   label: "Re-engage",     cx: 484, cy: 220, cluster: "phases",      shape: "rect" },
  { id: "dormant_node",         label: "Dormant",       cx: 612, cy: 220, cluster: "phases",      shape: "rounded" },

  // Layer 4 — Tool execution
  { id: "tool_node",            label: "Tools",         cx: 340, cy: 310, cluster: "tools",       shape: "hexagon" },

  // Layer 5 — Safety pipeline
  { id: "safety_gate",          label: "Safety",        cx: 196, cy: 390, cluster: "safety",      shape: "diamond" },
  { id: "retry_generation",     label: "Retry",         cx: 340, cy: 390, cluster: "safety",      shape: "rounded" },
  { id: "fallback_response",    label: "Fallback",      cx: 484, cy: 390, cluster: "safety",      shape: "rounded" },

  // Layer 6 — Persistence (standalone in DOT — visual cluster only)
  { id: "save_patient_context", label: "Save",          cx: 340, cy: 460, cluster: "persistence", shape: "rect" },
];

// ---------------------------------------------------------------------------
// Edges — 29 connections from architecture.dot
// ---------------------------------------------------------------------------

export const GRAPH_EDGES: GraphEdge[] = [
  // ── Entry ──
  { from: "__START__",           to: "consent_gate",         style: "solid",  color: "default" },

  // ── Ingestion pipeline (within cluster_ingestion) ──
  { from: "consent_gate",       to: "load_patient_context",  label: "allowed",      style: "solid",  color: "default" },
  { from: "load_patient_context", to: "crisis_check",                               style: "solid",  color: "default" },
  { from: "crisis_check",       to: "manage_history",        label: "safe",         style: "solid",  color: "default" },

  // ── Consent denied → END (skip edge) ──
  { from: "consent_gate",       to: "__END__",               label: "denied",       style: "dashed", color: "danger" },

  // ── Crisis → Fallback (skip edge) ──
  { from: "crisis_check",       to: "fallback_response",     label: "crisis!",      style: "dashed", color: "danger" },

  // ── Phase routing (from manage_history) ──
  { from: "manage_history",     to: "pending_node",          label: "PENDING",      style: "solid",  color: "phase" },
  { from: "manage_history",     to: "onboarding_agent",      label: "ONBOARDING",   style: "solid",  color: "phase" },
  { from: "manage_history",     to: "active_agent",          label: "ACTIVE",       style: "solid",  color: "phase" },
  { from: "manage_history",     to: "reengagement_agent",    label: "RE_ENGAGING",  style: "solid",  color: "phase" },
  { from: "manage_history",     to: "dormant_node",          label: "DORMANT",      style: "solid",  color: "phase" },

  // ── Simple phase paths → save ──
  { from: "pending_node",       to: "save_patient_context",  label: "template",     style: "solid",  color: "default" },
  { from: "dormant_node",       to: "save_patient_context",  label: "no-op",        style: "dashed", color: "default" },

  // ── Dormant with message → safety ──
  { from: "dormant_node",       to: "safety_gate",           label: "has msg",      style: "solid",  color: "default" },

  // ── Agent → Tool (forward) ──
  { from: "onboarding_agent",   to: "tool_node",             label: "tool call",    style: "solid",  color: "warning" },
  { from: "active_agent",       to: "tool_node",             label: "tool call",    style: "solid",  color: "warning" },
  { from: "reengagement_agent", to: "tool_node",             label: "tool call",    style: "solid",  color: "warning" },

  // ── Tool → Agent (back-edges) ──
  { from: "tool_node",          to: "onboarding_agent",      style: "dashed", color: "warning", isBackEdge: true },
  { from: "tool_node",          to: "active_agent",          style: "dashed", color: "warning", isBackEdge: true },
  { from: "tool_node",          to: "reengagement_agent",    style: "dashed", color: "warning", isBackEdge: true },

  // ── Agent done → Safety ──
  { from: "onboarding_agent",   to: "safety_gate",           label: "done",         style: "solid",  color: "default" },
  { from: "active_agent",       to: "safety_gate",           label: "done",         style: "solid",  color: "default" },
  { from: "reengagement_agent", to: "safety_gate",           label: "done",         style: "solid",  color: "default" },

  // ── Safety pipeline ──
  { from: "safety_gate",        to: "save_patient_context",  label: "pass",         style: "solid",  color: "success" },
  { from: "safety_gate",        to: "retry_generation",      label: "retry",        style: "solid",  color: "warning" },
  { from: "safety_gate",        to: "fallback_response",     label: "fail",         style: "solid",  color: "danger" },

  // ── Retry → Safety (back-edge) ──
  { from: "retry_generation",   to: "safety_gate",           label: "re-check",     style: "dashed", color: "warning", isBackEdge: true },

  // ── Fallback → Save ──
  { from: "fallback_response",  to: "save_patient_context",  style: "solid",  color: "default" },

  // ── Exit ──
  { from: "save_patient_context", to: "__END__",             style: "solid",  color: "default" },
];

// ---------------------------------------------------------------------------
// Clusters
// ---------------------------------------------------------------------------

export const CLUSTERS: ClusterDef[] = [
  { id: "ingestion",   label: "Ingestion Pipeline", bgColor: "#EFF6FF", borderColor: "#93C5FD" },
  { id: "phases",      label: "Phase Agents",       bgColor: "#F5F3FF", borderColor: "#A78BFA" },
  { id: "tools",       label: "Tool Execution",     bgColor: "#FFFBEB", borderColor: "#FBBF24" },
  { id: "safety",      label: "Safety Pipeline",    bgColor: "#FEF2F2", borderColor: "#F87171" },
  { id: "persistence", label: "Persistence",        bgColor: "#ECFDF5", borderColor: "#6EE7B7" },
];

// ---------------------------------------------------------------------------
// Color maps
// ---------------------------------------------------------------------------

/** Dynamic node fill/stroke based on pipeline status */
export const STATUS_COLORS = {
  idle:     { fill: "#F3F4F6", stroke: "#D1D5DB" },
  running:  { fill: "#DBEAFE", stroke: "#3B82F6" },
  complete: { fill: "#D1FAE5", stroke: "#10B981" },
} as const;

/** Edge stroke colors by category */
export const EDGE_COLORS = {
  default: "#9CA3AF",
  danger:  "#EF4444",
  warning: "#D97706",
  success: "#10B981",
  phase:   "#7C3AED",
} as const;

// ---------------------------------------------------------------------------
// Terminal node positions (for START/END circles in SVG)
// ---------------------------------------------------------------------------

export const START_POS = { cx: 340, cy: 20 } as const;
export const END_POS   = { cx: 340, cy: 510 } as const;
