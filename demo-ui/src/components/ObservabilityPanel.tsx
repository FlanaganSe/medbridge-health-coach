import { CircleCheck } from "lucide-react";
import type { PatientState } from "../types";
import {
  AlertBadge,
  JobStatusBadge,
  PhaseBadge,
  RoutineBadge,
  SafetyBadge,
} from "./ui/Badge";

interface ObservabilityPanelProps {
  state: PatientState;
  loadState: "loading" | "loaded" | "error";
  lastUpdated: Date | null;
  onRetry?: () => void;
}

function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], {
    hour: "numeric",
    minute: "2-digit",
  });
}

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString([], {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function SectionHeader({
  title,
  count,
  countVariant = "default",
}: {
  title: string;
  count?: number;
  countVariant?: "default" | "danger";
}) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-xs font-semibold tracking-wide text-text-secondary uppercase">
        {title}
      </span>
      {count !== undefined && (
        <span
          className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${
            countVariant === "danger"
              ? "bg-red-badge-bg text-red-badge-text"
              : "bg-bg-subtle text-text-secondary"
          }`}
        >
          {count}
        </span>
      )}
    </div>
  );
}

function Section({
  children,
  border = true,
}: {
  children: React.ReactNode;
  border?: boolean;
}) {
  return (
    <div
      className={`flex flex-col gap-3 px-5 py-4 ${border ? "border-b border-border" : ""}`}
    >
      {children}
    </div>
  );
}

function EmptyState({ text }: { text: string }) {
  return <div className="text-xs italic text-text-muted">{text}</div>;
}

const GREEN_OUTCOMES = new Set(["allowed", "safe", "success"]);
const RED_OUTCOMES = new Set(["denied", "block", "blocked", "clinical_boundary", "crisis"]);

function AuditOutcomeBadge({ outcome }: { outcome: string }) {
  const color = GREEN_OUTCOMES.has(outcome)
    ? "bg-green-badge-bg text-green-badge-text"
    : RED_OUTCOMES.has(outcome)
      ? "bg-red-badge-bg text-red-badge-text"
      : "bg-amber-badge-bg text-amber-badge-text";

  return (
    <span className={`rounded-full px-2 py-0.5 text-[11px] font-semibold ${color}`}>
      {outcome}
    </span>
  );
}

export function ObservabilityPanel({
  state,
  loadState,
  lastUpdated,
  onRetry,
}: ObservabilityPanelProps) {
  const urgentCount = state.alerts.filter(
    (a) => a.priority === "urgent",
  ).length;

  return (
    <div className="flex w-[420px] shrink-0 flex-col overflow-y-auto bg-white">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-border px-5 py-4">
        <span className="font-heading text-[15px] font-semibold text-text-primary">
          Observability
        </span>
        {loadState === "loading" && (
          <span className="text-xs text-text-muted">Loading...</span>
        )}
        {loadState === "error" && (
          <span className="flex items-center gap-2">
            <span className="text-xs text-red-badge-text">Error</span>
            {onRetry && (
              <button
                onClick={onRetry}
                className="text-xs text-blue-badge-text hover:underline"
              >
                Retry
              </button>
            )}
          </span>
        )}
        {loadState === "loaded" && lastUpdated && (
          <span className="text-xs text-text-muted">
            {lastUpdated.toLocaleTimeString([], {
              hour: "numeric",
              minute: "2-digit",
            })}
          </span>
        )}
      </div>

      {/* Phase */}
      <Section>
        <SectionHeader title="Phase" />
        <PhaseBadge phase={state.phase} />
      </Section>

      {/* Goals */}
      <Section>
        <SectionHeader title="Goals" count={state.goals.length} />
        {state.goals.length === 0 && <EmptyState text="No goals set" />}
        {state.goals.map((g) => (
          <div key={g.id} className="flex items-start gap-2.5">
            <CircleCheck size={16} className="mt-0.5 shrink-0 text-green-dot" />
            <div className="min-w-0 flex-1">
              <div className="text-[13px] font-medium text-text-primary">
                {g.goal_text}
              </div>
              <div className="text-[11px] text-text-muted">
                {g.confirmed_at
                  ? `Confirmed \u00b7 ${formatDate(g.confirmed_at)}`
                  : `Created \u00b7 ${formatDate(g.created_at)}`}
              </div>
            </div>
          </div>
        ))}
      </Section>

      {/* Alerts */}
      <Section>
        <SectionHeader
          title="Alerts"
          count={state.alerts.length}
          countVariant={urgentCount > 0 ? "danger" : "default"}
        />
        {state.alerts.length === 0 && <EmptyState text="No alerts" />}
        {state.alerts.map((a) => (
          <div key={a.id} className="flex items-start gap-2.5">
            {a.priority === "urgent" ? <AlertBadge /> : <RoutineBadge />}
            <div className="min-w-0 flex-1">
              <div className="text-xs leading-relaxed text-text-primary">
                {a.reason}
              </div>
              <div className="text-[11px] text-text-muted">
                {formatTime(a.created_at)}
              </div>
            </div>
          </div>
        ))}
      </Section>

      {/* Safety Decisions */}
      <Section>
        <SectionHeader
          title="Safety Decisions"
          count={state.safetyDecisions.length}
        />
        {state.safetyDecisions.length === 0 && (
          <EmptyState text="No decisions" />
        )}
        {state.safetyDecisions.slice(0, 5).map((d) => (
          <div key={d.id} className="flex items-center gap-2.5">
            <SafetyBadge decision={d.decision} />
            <div className="min-w-0 flex-1">
              <div className="text-xs text-text-secondary">{d.source}</div>
              <div className="text-[11px] text-text-muted">
                confidence: {d.confidence.toFixed(2)}
              </div>
            </div>
          </div>
        ))}
        {state.safetyDecisions.length > 5 && (
          <div className="text-xs font-medium text-text-secondary">
            +{state.safetyDecisions.length - 5} more decisions
          </div>
        )}
      </Section>

      {/* Scheduled Jobs */}
      <Section>
        <SectionHeader
          title="Scheduled Jobs"
          count={state.scheduledJobs.length}
        />
        {state.scheduledJobs.length === 0 && (
          <EmptyState text="No scheduled jobs" />
        )}
        {state.scheduledJobs.map((j) => (
          <div key={j.id} className="flex items-center justify-between">
            <div>
              <div className="font-mono text-xs font-medium text-text-primary">
                {j.job_type}
              </div>
              <div className="text-[11px] text-text-muted">
                {formatTime(j.scheduled_at)}
                {j.attempts > 0 && (
                  <span className="ml-2">
                    {j.attempts}/{j.max_attempts} attempts
                  </span>
                )}
              </div>
            </div>
            <JobStatusBadge status={j.status} />
          </div>
        ))}
      </Section>

      {/* Audit Trail */}
      <Section border={false}>
        <SectionHeader
          title="Audit Trail"
          count={state.auditEvents.length}
        />
        {state.auditEvents.length === 0 && (
          <EmptyState text="No audit events" />
        )}
        {state.auditEvents.slice(0, 10).map((e) => (
          <div key={e.id} className="flex items-center justify-between">
            <div>
              <div className="font-mono text-xs font-medium text-text-primary">
                {e.event_type}
              </div>
              <div className="text-[11px] text-text-muted">
                {formatTime(e.created_at)}
              </div>
            </div>
            <AuditOutcomeBadge outcome={e.outcome} />
          </div>
        ))}
        {state.auditEvents.length > 10 && (
          <div className="text-xs font-medium text-text-secondary">
            +{state.auditEvents.length - 10} more events
          </div>
        )}
      </Section>
    </div>
  );
}
