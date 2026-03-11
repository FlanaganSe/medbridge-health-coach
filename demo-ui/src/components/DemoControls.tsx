import { useState, useCallback } from "react";

interface DemoControlsProps {
  patientId: string;
  tenantId: string;
  onPatientSeeded: (id: string) => void;
}

interface ScheduledJob {
  id: string;
  job_type: string;
  status: string;
  scheduled_at: string;
  attempts: number;
  max_attempts: number;
}

type Status = "idle" | "loading" | "success" | "error";

export function DemoControls({
  patientId,
  tenantId,
  onPatientSeeded,
}: DemoControlsProps) {
  const [expanded, setExpanded] = useState(true);
  const [seedStatus, setSeedStatus] = useState<Status>("idle");
  const [triggerStatus, setTriggerStatus] = useState<Status>("idle");
  const [resetStatus, setResetStatus] = useState<Status>("idle");
  const [jobs, setJobs] = useState<ScheduledJob[]>([]);
  const [statusMessage, setStatusMessage] = useState("");

  const showStatus = useCallback((msg: string) => {
    setStatusMessage(msg);
    setTimeout(() => setStatusMessage(""), 3000);
  }, []);

  const seedPatient = useCallback(async () => {
    setSeedStatus("loading");
    try {
      const res = await fetch("/v1/demo/seed-patient", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          tenant_id: tenantId,
          external_patient_id: patientId,
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      onPatientSeeded(data.patient_id);
      setSeedStatus("success");
      showStatus(`Patient seeded: ${data.phase}`);
    } catch (err) {
      setSeedStatus("error");
      showStatus(`Seed failed: ${err}`);
    }
  }, [patientId, tenantId, onPatientSeeded, showStatus]);

  const fetchJobs = useCallback(async () => {
    try {
      const res = await fetch(`/v1/demo/scheduled-jobs/${patientId}`);
      if (!res.ok) return;
      const data = await res.json();
      setJobs(data.jobs ?? []);
    } catch {
      // Silently ignore fetch errors for jobs list
    }
  }, [patientId]);

  const triggerFollowup = useCallback(async () => {
    setTriggerStatus("loading");
    try {
      const res = await fetch(`/v1/demo/trigger-followup/${patientId}`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setTriggerStatus("success");
      showStatus(`Triggered ${data.job_type} (was due: ${data.original_scheduled_at})`);
      fetchJobs();
    } catch (err) {
      setTriggerStatus("error");
      showStatus(`Trigger failed: ${err}`);
    }
  }, [patientId, showStatus, fetchJobs]);

  const resetPatient = useCallback(async () => {
    setResetStatus("loading");
    try {
      const res = await fetch(`/v1/demo/reset-patient/${patientId}`, {
        method: "POST",
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setResetStatus("success");
      showStatus(
        `Reset: ${data.deleted_goals} goals, ${data.deleted_jobs} jobs, ${data.deleted_outbox} outbox`
      );
      setJobs([]);
    } catch (err) {
      setResetStatus("error");
      showStatus(`Reset failed: ${err}`);
    }
  }, [patientId, showStatus]);

  const statusColor: Record<string, string> = {
    pending: "#fbbf24",
    processing: "#3b82f6",
    completed: "#22c55e",
    cancelled: "#9ca3af",
    dead: "#ef4444",
  };

  return (
    <div
      style={{
        borderBottom: "1px solid #e5e7eb",
        background: "#fefce8",
        fontSize: 13,
      }}
    >
      <button
        onClick={() => setExpanded(!expanded)}
        style={{
          width: "100%",
          padding: "8px 16px",
          border: "none",
          background: "transparent",
          cursor: "pointer",
          textAlign: "left",
          fontWeight: 600,
          fontSize: 13,
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span>Demo Controls</span>
        <span>{expanded ? "\u25B2" : "\u25BC"}</span>
      </button>

      {expanded && (
        <div style={{ padding: "0 16px 12px" }}>
          <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
            <ActionButton
              label="Seed Patient"
              status={seedStatus}
              onClick={seedPatient}
            />
            <ActionButton
              label="Trigger Follow-up"
              status={triggerStatus}
              onClick={triggerFollowup}
            />
            <ActionButton
              label="Reset Patient"
              status={resetStatus}
              onClick={resetPatient}
            />
            <ActionButton
              label="Refresh Jobs"
              status="idle"
              onClick={fetchJobs}
            />
          </div>

          {statusMessage && (
            <div
              style={{
                padding: "4px 8px",
                background: "#fef3c7",
                borderRadius: 4,
                marginBottom: 8,
                fontSize: 12,
              }}
            >
              {statusMessage}
            </div>
          )}

          {jobs.length > 0 && (
            <div>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>
                Scheduled Jobs ({jobs.length})
              </div>
              <div
                style={{
                  maxHeight: 120,
                  overflow: "auto",
                  fontSize: 11,
                  fontFamily: "monospace",
                }}
              >
                {jobs.map((j) => (
                  <div
                    key={j.id}
                    style={{
                      display: "flex",
                      gap: 8,
                      padding: "2px 0",
                      alignItems: "center",
                    }}
                  >
                    <span
                      style={{
                        display: "inline-block",
                        width: 8,
                        height: 8,
                        borderRadius: "50%",
                        background: statusColor[j.status] ?? "#9ca3af",
                      }}
                    />
                    <span style={{ minWidth: 140 }}>{j.job_type}</span>
                    <span style={{ minWidth: 70, color: "#6b7280" }}>
                      {j.status}
                    </span>
                    <span style={{ color: "#9ca3af" }}>
                      {new Date(j.scheduled_at).toLocaleTimeString()}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ActionButton({
  label,
  status,
  onClick,
}: {
  label: string;
  status: Status;
  onClick: () => void;
}) {
  const isLoading = status === "loading";
  return (
    <button
      onClick={onClick}
      disabled={isLoading}
      style={{
        padding: "4px 10px",
        border: "1px solid #d1d5db",
        borderRadius: 4,
        background: isLoading ? "#e5e7eb" : "white",
        cursor: isLoading ? "default" : "pointer",
        fontSize: 12,
        whiteSpace: "nowrap",
      }}
    >
      {isLoading ? "..." : label}
    </button>
  );
}
