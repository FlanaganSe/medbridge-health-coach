import { Check, Circle, CircleCheck, Loader2 } from "lucide-react";
import { clsx } from "clsx";
import { useState } from "react";
import type { PipelineNode } from "../types";

interface PipelineStepperProps {
  nodes: PipelineNode[];
  isStreaming: boolean;
}

export function PipelineStepper({ nodes, isStreaming }: PipelineStepperProps) {
  const [expanded, setExpanded] = useState(true);

  if (nodes.length === 0) return null;

  const done = !isStreaming;

  // Collapsed summary bar
  if (!expanded) {
    return (
      <button
        onClick={() => setExpanded(true)}
        className={clsx(
          "flex w-full items-center gap-2 border-b border-border px-6 py-2 text-[11px] hover:opacity-80",
          done
            ? "bg-green-badge-bg text-green-badge-text"
            : "bg-blue-badge-bg text-blue-badge-text",
        )}
      >
        {done && <Check size={12} />}
        <span>
          {done
            ? `Pipeline completed (${nodes.length} nodes)`
            : `Pipeline running (${nodes.length} nodes)`}
        </span>
        <span className="ml-auto text-text-muted">click to expand</span>
      </button>
    );
  }

  return (
    <div className="border-b border-border bg-bg-faint px-6 py-3">
      <div className="flex items-center gap-2">
        <span className="text-[11px] font-semibold tracking-wide text-text-secondary">
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

      <ol className="mt-2 flex flex-col" aria-label="Pipeline steps" aria-live="polite">
        {nodes.map((node, i) => {
          const isLast = i === nodes.length - 1;
          return (
            <li key={node.name} className="flex items-start gap-2.5">
              <div className="flex flex-col items-center">
                {node.status === "complete" ? (
                  <CircleCheck size={16} className="shrink-0 text-green-badge-text" />
                ) : node.status === "running" ? (
                  <>
                    <Loader2
                      size={16}
                      className="shrink-0 animate-spin text-blue-badge-text"
                      aria-hidden="true"
                    />
                    <span className="sr-only">Running</span>
                  </>
                ) : (
                  <Circle size={16} className="shrink-0 text-text-muted" />
                )}
                {!isLast && <div className="min-h-2 w-px flex-1 bg-border" />}
              </div>
              <span
                className={clsx(
                  "pb-2 text-[13px] leading-4",
                  node.status === "complete" && "font-medium text-green-badge-text",
                  node.status === "running" && "font-medium text-blue-badge-text",
                  node.status === "pending" && "text-text-muted",
                )}
              >
                {node.displayName}
              </span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
