import { useEffect, useRef, useState } from "react";
import {
  ShieldCheck,
  TriangleAlert,
  type LucideIcon,
} from "lucide-react";
import clsx from "clsx";
import type { Phase, SafetyDecision } from "../../types";

// --- Phase Badge ---

const PHASE_STYLES: Record<Phase, { bg: string; text: string; dot: string }> = {
  pending: {
    bg: "bg-bg-muted",
    text: "text-text-secondary",
    dot: "bg-text-secondary",
  },
  onboarding: {
    bg: "bg-teal-light",
    text: "text-teal",
    dot: "bg-teal",
  },
  active: {
    bg: "bg-green-light",
    text: "text-green",
    dot: "bg-green",
  },
  re_engaging: {
    bg: "bg-orange-light",
    text: "text-orange",
    dot: "bg-orange",
  },
  dormant: {
    bg: "bg-bg-muted",
    text: "text-text-secondary",
    dot: "bg-text-muted",
  },
};

export function PhaseBadge({ phase }: { phase: Phase }) {
  const prevPhaseRef = useRef<Phase | null>(null);
  const [transitioning, setTransitioning] = useState(false);

  useEffect(() => {
    if (prevPhaseRef.current !== null && prevPhaseRef.current !== phase) {
      setTransitioning(true);
      const timer = setTimeout(() => setTransitioning(false), 600);
      prevPhaseRef.current = phase;
      return () => clearTimeout(timer);
    }
    prevPhaseRef.current = phase;
  }, [phase]);

  const style = PHASE_STYLES[phase] ?? PHASE_STYLES.pending;
  return (
    <span
      className={clsx(
        "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-[11px] font-semibold tracking-wide uppercase",
        style.bg,
        style.text,
        transitioning && "animate-phase-pulse",
      )}
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
      bg="bg-red-light"
      text="text-red"
    />
  );
}

export function RoutineBadge() {
  return (
    <StatusBadge
      label="ROUTINE"
      bg="bg-teal-light"
      text="text-teal"
    />
  );
}

// --- Safety Decision Badge (dynamic) ---

const SAFETY_STYLES: Record<
  SafetyDecision,
  { bg: string; text: string; Icon?: LucideIcon }
> = {
  safe: {
    bg: "bg-green-light",
    text: "text-green",
    Icon: ShieldCheck,
  },
  clinical_boundary: {
    bg: "bg-red-light",
    text: "text-red",
    Icon: TriangleAlert,
  },
  crisis: {
    bg: "bg-red-light",
    text: "text-red",
    Icon: TriangleAlert,
  },
  jailbreak: {
    bg: "bg-red-light",
    text: "text-red",
    Icon: TriangleAlert,
  },
  fallback: {
    bg: "bg-red-light",
    text: "text-red",
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
    <span className="inline-flex items-center gap-1.5 rounded bg-green-light px-2 py-0.5 text-[11px] font-medium text-green">
      <span className="h-1.5 w-1.5 rounded-full bg-green" />
      completed
    </span>
  );
}

export function JobPendingBadge() {
  return (
    <span className="inline-flex items-center gap-1.5 rounded bg-orange-light px-2 py-0.5 text-[11px] font-medium text-orange">
      <span className="h-1.5 w-1.5 rounded-full bg-orange" />
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
    <span className="inline-flex items-center gap-1.5 rounded bg-bg-muted px-2 py-0.5 text-[11px] font-medium text-text-secondary">
      <span className="h-1.5 w-1.5 rounded-full bg-text-muted" />
      {status}
    </span>
  );
}
