import { useState, useEffect, useCallback } from "react";

interface SidebarProps {
  patientId: string;
  tenantId: string;
}

interface PatientState {
  phase: string;
  goals: Array<{ id: string; goal_text: string; created_at: string }>;
  alerts: Array<{
    id: string;
    reason: string;
    priority: string;
    created_at: string;
  }>;
  safetyDecisions: Array<{
    id: string;
    decision: string;
    source: string;
    created_at: string;
  }>;
}

const HEADERS = (patientId: string, tenantId: string) => ({
  "X-Patient-ID": patientId,
  "X-Tenant-ID": tenantId,
});

export function ObservabilitySidebar({ patientId, tenantId }: SidebarProps) {
  const [state, setState] = useState<PatientState>({
    phase: "unknown",
    goals: [],
    alerts: [],
    safetyDecisions: [],
  });

  const fetchState = useCallback(async () => {
    const headers = HEADERS(patientId, tenantId);
    try {
      const [phaseRes, goalsRes, alertsRes, safetyRes] = await Promise.all([
        fetch(`/v1/patients/${patientId}/phase`, { headers }),
        fetch(`/v1/patients/${patientId}/goals`, { headers }),
        fetch(`/v1/patients/${patientId}/alerts`, { headers }),
        fetch(`/v1/patients/${patientId}/safety-decisions`, { headers }),
      ]);

      const phase = phaseRes.ok ? (await phaseRes.json()).phase : "error";
      const goals = goalsRes.ok ? (await goalsRes.json()).goals : [];
      const alerts = alertsRes.ok ? (await alertsRes.json()).alerts : [];
      const safetyDecisions = safetyRes.ok
        ? (await safetyRes.json()).decisions
        : [];

      setState({ phase, goals, alerts, safetyDecisions });
    } catch {
      // Silently handle fetch errors in demo UI
    }
  }, [patientId, tenantId]);

  useEffect(() => {
    fetchState();
    const interval = setInterval(fetchState, 5000);
    return () => clearInterval(interval);
  }, [fetchState]);

  return (
    <div
      style={{
        width: 320,
        borderLeft: "1px solid #e5e7eb",
        overflow: "auto",
        padding: 16,
        fontSize: 13,
      }}
    >
      <h2 style={{ margin: "0 0 12px", fontSize: 16 }}>Observability</h2>

      <Section title="Phase">
        <span
          style={{
            padding: "2px 8px",
            borderRadius: 4,
            background: "#dbeafe",
            fontWeight: 600,
          }}
        >
          {state.phase}
        </span>
      </Section>

      <Section title={`Goals (${state.goals.length})`}>
        {state.goals.map((g) => (
          <div key={g.id} style={{ marginBottom: 4 }}>
            {g.goal_text}
          </div>
        ))}
        {state.goals.length === 0 && <Muted>No goals set</Muted>}
      </Section>

      <Section title={`Alerts (${state.alerts.length})`}>
        {state.alerts.map((a) => (
          <div
            key={a.id}
            style={{
              marginBottom: 4,
              padding: 4,
              background: a.priority === "urgent" ? "#fef2f2" : "#f9fafb",
              borderRadius: 4,
            }}
          >
            <strong>{a.priority}</strong>: {a.reason}
          </div>
        ))}
        {state.alerts.length === 0 && <Muted>No alerts</Muted>}
      </Section>

      <Section title={`Safety (${state.safetyDecisions.length})`}>
        {state.safetyDecisions.slice(0, 10).map((d) => (
          <div key={d.id} style={{ marginBottom: 4 }}>
            {d.decision} ({d.source})
          </div>
        ))}
        {state.safetyDecisions.length === 0 && <Muted>No decisions</Muted>}
      </Section>
    </div>
  );
}

function Section({
  title,
  children,
}: {
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div style={{ marginBottom: 16 }}>
      <h3 style={{ margin: "0 0 6px", fontSize: 13, color: "#6b7280" }}>
        {title}
      </h3>
      {children}
    </div>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <div style={{ color: "#9ca3af", fontStyle: "italic" }}>{children}</div>;
}
