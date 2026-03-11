import {
  ShieldCheck,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import type { Phase, SafetyDecision } from "../../types";

// --- Phase Badge ---

const PHASE_STYLES: Record<Phase, { bg: string; text: string; dot: string }> = {
  pending: {
    bg: "bg-bg-subtle",
    text: "text-text-secondary",
    dot: "bg-text-secondary",
  },
  onboarding: {
    bg: "bg-blue-badge-bg",
    text: "text-blue-badge-text",
    dot: "bg-blue-badge-text",
  },
  active: {
    bg: "bg-green-badge-bg",
    text: "text-green-badge-text",
    dot: "bg-green-dot",
  },
  re_engaging: {
    bg: "bg-amber-badge-bg",
    text: "text-amber-badge-text",
    dot: "bg-amber-dot",
  },
  dormant: {
    bg: "bg-bg-subtle",
    text: "text-text-secondary",
    dot: "bg-text-muted",
  },
};

export function PhaseBadge({ phase }: { phase: Phase }) {
  const style = PHASE_STYLES[phase] ?? PHASE_STYLES.pending;
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[11px] font-semibold tracking-wide uppercase ${style.bg} ${style.text}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} />
      {phase.replace(/_/g, " ")}
    </span>
  );
}

// --- Alert / Safe / Routine Badges ---

interface StatusBadgeProps {
  label: string;
  Icon?: LucideIcon;
  bg: string;
  text: string;
}

function StatusBadge({ label, Icon, bg, text }: StatusBadgeProps) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[11px] font-semibold tracking-wide uppercase ${bg} ${text}`}
    >
      {Icon && <Icon size={12} />}
      {label}
    </span>
  );
}

export function AlertBadge() {
  return (
    <StatusBadge
      label="URGENT"
      Icon={TriangleAlert}
      bg="bg-red-badge-bg"
      text="text-red-badge-text"
    />
  );
}

export function RoutineBadge() {
  return (
    <StatusBadge
      label="ROUTINE"
      bg="bg-blue-badge-bg"
      text="text-blue-badge-text"
    />
  );
}

// --- Safety Decision Badge (dynamic) ---

const SAFETY_STYLES: Record<
  SafetyDecision,
  { bg: string; text: string; Icon?: LucideIcon }
> = {
  safe: {
    bg: "bg-green-badge-bg",
    text: "text-green-badge-text",
    Icon: ShieldCheck,
  },
  block: { bg: "bg-red-badge-bg", text: "text-red-badge-text" },
  clinical_boundary: {
    bg: "bg-red-badge-bg",
    text: "text-red-badge-text",
    Icon: TriangleAlert,
  },
  crisis: {
    bg: "bg-red-badge-bg",
    text: "text-red-badge-text",
    Icon: TriangleAlert,
  },
};

export function SafetyBadge({ decision }: { decision: SafetyDecision }) {
  const style = SAFETY_STYLES[decision] ?? SAFETY_STYLES.safe;
  return (
    <StatusBadge
      label={decision.replace(/_/g, " ")}
      Icon={style.Icon}
      bg={style.bg}
      text={style.text}
    />
  );
}

// --- Job Status Badges ---

export function JobCompleteBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded bg-green-badge-bg px-2 py-0.5 text-[11px] font-medium text-green-badge-text">
      <span className="h-1.5 w-1.5 rounded-full bg-green-dot" />
      completed
    </span>
  );
}

export function JobPendingBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded bg-amber-badge-bg px-2 py-0.5 text-[11px] font-medium text-amber-badge-text">
      <span className="h-1.5 w-1.5 rounded-full bg-amber-dot" />
      pending
    </span>
  );
}

export function JobStatusBadge({
  status,
}: {
  status: "pending" | "processing" | "completed" | "cancelled" | "dead";
}) {
  if (status === "completed") return <JobCompleteBadge />;
  if (status === "pending" || status === "processing")
    return <JobPendingBadge />;
  return (
    <span className="inline-flex items-center gap-1.5 rounded bg-bg-subtle px-2 py-0.5 text-[11px] font-medium text-text-secondary">
      <span className="h-1.5 w-1.5 rounded-full bg-text-muted" />
      {status}
    </span>
  );
}
