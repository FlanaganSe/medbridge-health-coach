import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import type { PipelineNode } from "../types";
import {
  CLUSTERS,
  EDGE_COLORS,
  END_POS,
  GRAPH_EDGES,
  GRAPH_NODES,
  START_POS,
  STATUS_COLORS,
  VIEWBOX,
} from "./graphLayout";

// Node dimensions (px in SVG coordinate space)
const NODE_W = 90;
const NODE_H = 32;
const HALF_W = NODE_W / 2;
const HALF_H = NODE_H / 2;
const TERMINAL_R = 8;

interface GraphViewProps {
  nodes: PipelineNode[];
  isStreaming: boolean;
}

/** Resolve a node ID (including __START__/__END__) to its center position. */
function nodePos(id: string): { cx: number; cy: number } | null {
  if (id === "__START__") return { cx: START_POS.cx, cy: START_POS.cy };
  if (id === "__END__") return { cx: END_POS.cx, cy: END_POS.cy };
  return GRAPH_NODES.find((n) => n.id === id) ?? null;
}

/** Compute a cubic bezier path for a back-edge. */
function backEdgePath(
  from: { cx: number; cy: number },
  to: { cx: number; cy: number },
): string {
  if (from.cy === to.cy) {
    // Same-row back-edge (retry → safety): arc below the row
    const x1 = from.cx - HALF_W;
    const x2 = to.cx + HALF_W;
    return `M ${x1},${from.cy} C ${x1},${from.cy + 40} ${x2},${to.cy + 40} ${x2},${to.cy}`;
  }
  // Upward back-edge (tool → agent): curve to the side
  const x1 = from.cx;
  const y1 = from.cy - HALF_H;
  const x2 = to.cx;
  const y2 = to.cy + HALF_H;
  const midY = (y1 + y2) / 2;
  const dx = to.cx - from.cx;
  const offset = dx === 0 ? 35 : dx > 0 ? 25 : -25;
  return `M ${x1},${y1} C ${x1 + offset},${midY} ${x2 + offset},${midY} ${x2},${y2}`;
}

export function GraphView({ nodes, isStreaming }: GraphViewProps) {
  const [expanded, setExpanded] = useState(true);

  // Re-expand when a new stream starts
  useEffect(() => {
    if (isStreaming) setExpanded(true);
  }, [isStreaming]);

  if (nodes.length === 0) return null;

  const done = !isStreaming;

  // Collapsed summary bar
  if (done && !expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className="flex w-full items-center gap-2 border-b border-border bg-green-badge-bg px-6 py-2 text-[11px] text-green-badge-text hover:bg-green-badge-bg/80"
      >
        <Check size={12} />
        <span>
          Pipeline completed ({nodes.length} nodes)
        </span>
        <span className="ml-auto text-text-muted">click to expand</span>
      </button>
    );
  }

  return (
    <div className="border-b border-border bg-bg-faint px-2 py-3">
      <div className="flex items-center gap-2 px-4">
        <span className="text-[11px] font-semibold text-text-secondary tracking-wide">
          ARCHITECTURE
        </span>
        {done && (
          <button
            onClick={() => setExpanded(false)}
            aria-label="Collapse graph"
            className="ml-auto text-[11px] text-text-muted hover:text-text-secondary"
          >
            collapse
          </button>
        )}
      </div>

      <svg
        viewBox={VIEWBOX}
        width="100%"
        className="mt-2"
        role="img"
        aria-label="Agent pipeline graph"
      >
        {/* Arrow markers for each edge color */}
        <defs>
          {Object.entries(EDGE_COLORS).map(([key, hex]) => (
            <marker
              key={key}
              id={`arrow-${key}`}
              viewBox="0 0 10 7"
              refX="10"
              refY="3.5"
              markerWidth="8"
              markerHeight="6"
              orient="auto"
            >
              <polygon points="0 0, 10 3.5, 0 7" style={{ fill: hex }} />
            </marker>
          ))}
        </defs>

        {/* 1. Cluster backgrounds */}
        {CLUSTERS.map((cluster) => {
          const members = GRAPH_NODES.filter((n) => n.cluster === cluster.id);
          if (members.length === 0) return null;
          const pad = 20;
          const x1 = Math.min(...members.map((n) => n.cx)) - HALF_W - pad;
          const y1 = Math.min(...members.map((n) => n.cy)) - HALF_H - pad;
          const x2 = Math.max(...members.map((n) => n.cx)) + HALF_W + pad;
          const y2 = Math.max(...members.map((n) => n.cy)) + HALF_H + pad;
          return (
            <g key={cluster.id}>
              <rect
                x={x1}
                y={y1}
                width={x2 - x1}
                height={y2 - y1}
                rx={8}
                style={{
                  fill: cluster.bgColor,
                  stroke: cluster.borderColor,
                  strokeWidth: 1.5,
                  strokeDasharray: "6 3",
                }}
              />
              <text
                x={x1 + 8}
                y={y1 + 14}
                style={{ fontSize: 9, fontWeight: 600, fill: cluster.borderColor }}
              >
                {cluster.label}
              </text>
            </g>
          );
        })}

        {/* 2. Edges */}
        {GRAPH_EDGES.map((edge, i) => {
          const from = nodePos(edge.from);
          const to = nodePos(edge.to);
          if (!from || !to) return null;

          const color = EDGE_COLORS[edge.color];
          const dash = edge.style === "dashed" ? "5 3" : undefined;
          const marker = `url(#arrow-${edge.color})`;

          // Back-edges: bezier curves
          if (edge.isBackEdge) {
            const d = backEdgePath(from, to);
            const labelX =
              from.cy === to.cy
                ? (from.cx + to.cx) / 2
                : (from.cx + to.cx) / 2 + (to.cx < from.cx ? -20 : to.cx > from.cx ? 20 : 30);
            const labelY = from.cy === to.cy ? from.cy + 35 : (from.cy + to.cy) / 2;
            return (
              <g key={i}>
                <path
                  d={d}
                  style={{
                    fill: "none",
                    stroke: color,
                    strokeWidth: 1.2,
                    strokeDasharray: dash,
                  }}
                  markerEnd={marker}
                />
                {edge.label && (
                  <text
                    x={labelX}
                    y={labelY}
                    textAnchor="middle"
                    style={{ fontSize: 7, fill: color, fontWeight: 500 }}
                  >
                    {edge.label}
                  </text>
                )}
              </g>
            );
          }

          // consent_gate → __END__: route along the right side to avoid center overlap
          if (edge.from === "consent_gate" && edge.to === "__END__") {
            const x1 = from.cx + HALF_W;
            const y1 = from.cy;
            const x2 = to.cx;
            const y2 = to.cy - TERMINAL_R;
            return (
              <g key={i}>
                <path
                  d={`M ${x1},${y1} C 665,${y1} 665,${y2} ${x2},${y2}`}
                  style={{
                    fill: "none",
                    stroke: color,
                    strokeWidth: 1.2,
                    strokeDasharray: dash,
                  }}
                  markerEnd={marker}
                />
                {edge.label && (
                  <text
                    x={660}
                    y={280}
                    textAnchor="end"
                    style={{ fontSize: 7, fill: color, fontWeight: 500 }}
                  >
                    {edge.label}
                  </text>
                )}
              </g>
            );
          }

          // Forward edges: straight lines
          const sx = from.cx;
          const sy = edge.from === "__START__" ? from.cy + TERMINAL_R : from.cy + HALF_H;
          const tx = to.cx;
          const ty = edge.to === "__END__" ? to.cy - TERMINAL_R : to.cy - HALF_H;

          return (
            <g key={i}>
              <line
                x1={sx}
                y1={sy}
                x2={tx}
                y2={ty}
                style={{
                  stroke: color,
                  strokeWidth: 1.2,
                  strokeDasharray: dash,
                }}
                markerEnd={marker}
              />
              {edge.label && (
                <text
                  x={(sx + tx) / 2}
                  y={(sy + ty) / 2 - 4}
                  textAnchor="middle"
                  style={{ fontSize: 7, fill: color, fontWeight: 500 }}
                >
                  {edge.label}
                </text>
              )}
            </g>
          );
        })}

        {/* 3. Terminal nodes (START/END) */}
        <circle
          cx={START_POS.cx}
          cy={START_POS.cy}
          r={TERMINAL_R}
          style={{ fill: "#111827" }}
        />
        <circle
          cx={END_POS.cx}
          cy={END_POS.cy}
          r={TERMINAL_R}
          style={{ fill: "#111827" }}
        />
        <circle
          cx={END_POS.cx}
          cy={END_POS.cy}
          r={TERMINAL_R - 3}
          style={{ fill: "#374151" }}
        />

        {/* 4. Pipeline nodes */}
        {GRAPH_NODES.map((node) => {
          const pn = nodes.find((n) => n.name === node.id);
          const status = pn?.status;
          const colors =
            status === "running"
              ? STATUS_COLORS.running
              : status === "complete"
                ? STATUS_COLORS.complete
                : STATUS_COLORS.idle;

          return (
            <g key={node.id}>
              <rect
                x={node.cx - HALF_W}
                y={node.cy - HALF_H}
                width={NODE_W}
                height={NODE_H}
                rx={6}
                className={status === "running" ? "animate-node-pulse" : undefined}
                style={{
                  fill: colors.fill,
                  stroke: colors.stroke,
                  strokeWidth: 1.5,
                }}
              />
              <text
                x={node.cx}
                y={node.cy + 4}
                textAnchor="middle"
                style={{
                  fontSize: 9,
                  fontWeight: 500,
                  fill: !status || status === "pending" ? "#6B7280" : "#111827",
                }}
              >
                {node.label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
