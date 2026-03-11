import { ShieldAlert, X } from "lucide-react";
import { useEffect } from "react";
import type { SafetyDecision } from "../types";

interface SafetyToastProps {
  toast: { decision: SafetyDecision; confidence: number } | null;
  onDismiss: () => void;
}

const LABELS: Record<string, string> = {
  safe: "Safe",
  block: "Blocked",
  clinical_boundary: "Clinical Boundary Detected",
  crisis: "Crisis Detected",
  fallback: "Fallback Response",
};

export function SafetyToast({ toast, onDismiss }: SafetyToastProps) {
  // Auto-dismiss after 5 seconds
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(onDismiss, 5000);
    return () => clearTimeout(timer);
  }, [toast, onDismiss]);

  if (!toast) return null;

  return (
    <div className="fixed right-6 top-20 z-50 animate-in-right w-80 rounded-lg border border-red-badge-text/20 bg-red-badge-bg p-4 shadow-lg">
      <div className="flex items-start gap-3">
        <ShieldAlert size={20} className="shrink-0 text-red-badge-text" />
        <div className="flex-1">
          <div className="text-[13px] font-semibold text-red-badge-text">
            {LABELS[toast.decision] ?? toast.decision}
          </div>
          {toast.confidence > 0 && (
            <div className="mt-0.5 text-[12px] text-text-secondary">
              Confidence: {(toast.confidence * 100).toFixed(0)}%
            </div>
          )}
          <div className="mt-1 text-[12px] text-text-secondary">
            Safety gate activated — response was filtered or escalated.
          </div>
        </div>
        <button
          onClick={onDismiss}
          aria-label="Dismiss"
          className="shrink-0 text-text-muted hover:text-text-secondary"
        >
          <X size={14} />
        </button>
      </div>
    </div>
  );
}
