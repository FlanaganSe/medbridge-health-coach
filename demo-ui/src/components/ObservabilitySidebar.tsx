import { useState, useEffect, useCallback, useRef } from "react";

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

type LoadState = "loading" | "loaded" | "error";

const POLL_MS = 2000;

const HEADERS = (patientId: string, tenantId: string) => ({
  "X-Patient-ID": patientId,
  "X-Tenant-ID": tenantId,
});

const PHASE_COLORS: Record<string, string> = {
  pending: "#fef3c7",
  onboarding: "#dbeafe",
  active: "#dcfce7",
  re_engaging: "#fce7f3",
  dormant: "#f3f4f6",
};

export function ObservabilitySidebar({ patientId, tenantId }: SidebarProps) {
  const [state, setState] = useState<PatientState>({
    phase: "unknown",
    goals: [],
    alerts: [],
    safetyDecisions: [],
  });
  const [loadState, setLoadState] = useState<LoadState>("loading");
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null);
  const mountedRef = useRef(true);

  const fetchState = useCallback(async () => {
    const headers = HEADERS(patientId, tenantId);
    try {
      const [phaseRes, goalsRes, alertsRes, safetyRes] = await Promise.all([
        fetch(`/v1/patients/${patientId}/phase`, { headers }),
        fetch(`/v1/patients/${patientId}/goals`, { headers }),
        fetch(`/v1/patients/${patientId}/alerts`, { headers }),
        fetch(`/v1/patients/${patientId}/safety-decisions`, { headers }),
      ]);

      if (!mountedRef.current) return;

      const phase = phaseRes.ok ? (await phaseRes.json()).phase : "error";
      const goals = goalsRes.ok ? (await goalsRes.json()).goals : [];
      const alerts = alertsRes.ok ? (await alertsRes.json()).alerts : [];
      const safetyDecisions = safetyRes.ok
        ? (await safetyRes.json()).decisions
        : [];

      setState({ phase, goals, alerts, safetyDecisions });
      setLoadState("loaded");
      setLastUpdated(new Date());
    } catch {
      if (mountedRef.current) {
        setLoadState("error");
      }
    }
  }, [patientId, tenantId]);

  useEffect(() => {
    mountedRef.current = true;
    setLoadState("loading");
    fetchState();
    const interval = setInterval(fetchState, POLL_MS);
    return () => {
      mountedRef.current = false;
      clearInterval(interval);
    };
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
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: 12,
        }}
      >
        <h2 style={{ margin: 0, fontSize: 16 }}>Observability</h2>
        {loadState === "loading" && (
          <span style={{ color: "#9ca3af", fontSize: 11 }}>Loading...</span>
        )}
        {loadState === "error" && (
          <span style={{ color: "#ef4444", fontSize: 11 }}>Error</span>
        )}
        {loadState === "loaded" && lastUpdated && (
          <span style={{ color: "#9ca3af", fontSize: 11 }}>
            {lastUpdated.toLocaleTimeString()}
          </span>
        )}
      </div>

      <Section title="Phase">
        <span
          style={{
            padding: "2px 8px",
            borderRadius: 4,
            background: PHASE_COLORS[state.phase] ?? "#f3f4f6",
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
            <span
              style={{
                display: "inline-block",
                padding: "1px 4px",
                borderRadius: 3,
                background:
                  d.decision === "allow" ? "#dcfce7" : "#fef2f2",
                fontSize: 11,
                marginRight: 4,
              }}
            >
              {d.decision}
            </span>
            <span style={{ color: "#6b7280" }}>{d.source}</span>
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
  return (
    <div style={{ color: "#9ca3af", fontStyle: "italic" }}>{children}</div>
  );
}
