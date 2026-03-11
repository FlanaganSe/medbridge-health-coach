import { Check } from "lucide-react";
import { useEffect, useState } from "react";
import { clsx } from "clsx";
import type { PipelineNode } from "../types";

interface PipelineTraceProps {
  nodes: PipelineNode[];
  isStreaming: boolean;
}

export function PipelineTrace({ nodes, isStreaming }: PipelineTraceProps) {
  const [expanded, setExpanded] = useState(true);

  // Re-expand when a new stream starts
  useEffect(() => {
    if (isStreaming) setExpanded(true);
  }, [isStreaming]);

  if (nodes.length === 0) return null;

  const done = !isStreaming;

  // When collapsed after completion, show a summary bar
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
    <div className="border-b border-border bg-bg-faint px-6 py-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold text-text-secondary tracking-wide">
          PIPELINE
        </span>
        {done && (
          <button
            onClick={() => setExpanded(false)}
            aria-label="Collapse pipeline"
            className="ml-auto text-[11px] text-text-muted hover:text-text-secondary"
          >
            collapse
          </button>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-1.5">
        {nodes.map((node) => (
          <span
            key={node.name}
            className={clsx(
              "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[11px] font-medium transition-colors",
              node.status === "complete" &&
                "bg-green-badge-bg text-green-badge-text",
              node.status === "running" &&
                "animate-pulse bg-blue-badge-bg text-blue-badge-text",
              node.status === "pending" &&
                "bg-bg-subtle text-text-muted",
            )}
          >
            {node.status === "complete" && <Check size={10} />}
            {node.displayName}
          </span>
        ))}
        {isStreaming && (
          <span className="inline-flex items-center gap-1 text-[11px] text-text-muted">
            <span className="animate-pulse">...</span>
          </span>
        )}
      </div>
    </div>
  );
}
