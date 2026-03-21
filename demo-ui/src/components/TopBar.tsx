import { HeartPulse, RefreshCw, RotateCcw, UserPlus, Zap } from "lucide-react";
import { clsx } from "clsx";
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api";
import type { ResetPatientResponse, TriggerFollowupResponse } from "../types";
import { Button } from "./ui/Button";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { PatientSelector } from "./ui/PatientSelector";

type Status = "idle" | "loading" | "success" | "error";

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

interface Patient {
  id: string;
  name: string;
}

interface TopBarProps {
  patients: Patient[];
  selectedPatientId: string;
  onPatientChange: (id: string) => void;
  patientId: string;
  externalPatientId: string;
  tenantId: string;
  onPatientSeeded: (id: string) => void;
  onStateChanged: () => void;
  onReset: () => void;
}

export function TopBar({
  patients,
  selectedPatientId,
  onPatientChange,
  patientId,
  externalPatientId,
  tenantId,
  onPatientSeeded,
  onStateChanged,
  onReset,
}: TopBarProps) {
  const [seedStatus, setSeedStatus] = useState<Status>("idle");
  const [triggerStatus, setTriggerStatus] = useState<Status>("idle");
  const [resetStatus, setResetStatus] = useState<Status>("idle");
  const [statusMessage, setStatusMessage] = useState<{
    text: string;
    type: "success" | "error";
  } | null>(null);
  const [confirmResetOpen, setConfirmResetOpen] = useState(false);
  const statusTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    return () => {
      if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
    };
  }, []);

  const showStatus = useCallback(
    (text: string, type: "success" | "error") => {
      setStatusMessage({ text, type });
      if (statusTimerRef.current) clearTimeout(statusTimerRef.current);
      statusTimerRef.current = setTimeout(() => setStatusMessage(null), 4000);
    },
    [],
  );

  const handleSeed = useCallback(async () => {
    setSeedStatus("loading");
    try {
      const data = await api.seedPatient(tenantId, externalPatientId);
      onPatientSeeded(data.patient_id);
      setSeedStatus("success");
      showStatus(`Patient seeded (${data.phase})`, "success");
      onStateChanged();
    } catch (err) {
      setSeedStatus("error");
      showStatus(`Seed failed: ${errorMessage(err)}`, "error");
    }
  }, [tenantId, externalPatientId, onPatientSeeded, showStatus, onStateChanged]);

  const handleTrigger = useCallback(async () => {
    setTriggerStatus("loading");
    try {
      const data: TriggerFollowupResponse =
        await api.triggerFollowup(patientId);
      setTriggerStatus("success");
      showStatus(`Expedited ${data.job_type}`, "success");
      onStateChanged();
    } catch (err) {
      setTriggerStatus("error");
      showStatus(`No pending check-ins: ${errorMessage(err)}`, "error");
    }
  }, [patientId, showStatus, onStateChanged]);

  const handleReset = useCallback(async () => {
    setConfirmResetOpen(false);
    setResetStatus("loading");
    try {
      const data: ResetPatientResponse = await api.resetPatient(patientId);
      setResetStatus("success");
      showStatus(
        `Reset: ${data.deleted_goals} goals, ${data.deleted_jobs} jobs removed`,
        "success",
      );
      onStateChanged();
      onReset();
    } catch (err) {
      setResetStatus("error");
      showStatus(`Reset failed: ${errorMessage(err)}`, "error");
    }
  }, [patientId, showStatus, onStateChanged, onReset]);

  return (
    <>
      <header className="flex shrink-0 items-center justify-between border-b border-border-primary bg-bg-card px-6 py-3">
        {/* Left: branding */}
        <div className="flex items-center gap-3">
          <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-teal">
            <HeartPulse size={16} className="text-white" />
          </div>
          <span className="font-heading text-xl font-semibold text-text-primary">
            Health Ally
          </span>
          <span className="rounded bg-orange-light px-2 py-0.5 font-mono text-[10px] font-bold tracking-wider text-orange">
            DEMO
          </span>
        </div>

        {/* Right: patient selector + actions */}
        <div className="flex items-center gap-3">
          <PatientSelector
            patients={patients}
            selectedId={selectedPatientId}
            onChange={onPatientChange}
          />

          <div className="h-6 w-px bg-border-primary" />

          <Button
            label="Seed Patient"
            icon={UserPlus}
            loading={seedStatus === "loading"}
            onClick={handleSeed}
          />
          <Button
            label="Run Check-in"
            icon={Zap}
            variant="primary"
            loading={triggerStatus === "loading"}
            onClick={handleTrigger}
          />
          <Button
            label="Reset"
            icon={RotateCcw}
            danger
            loading={resetStatus === "loading"}
            onClick={() => setConfirmResetOpen(true)}
          />
          <Button
            label="Refresh"
            icon={RefreshCw}
            onClick={onStateChanged}
          />
        </div>
      </header>

      {/* Status message */}
      {statusMessage && (
        <div
          className={clsx(
            "border-b border-border-primary px-6 py-2 text-[12px]",
            statusMessage.type === "success"
              ? "bg-green-light text-green"
              : "bg-red-light text-red",
          )}
        >
          {statusMessage.text}
        </div>
      )}

      <ConfirmDialog
        open={confirmResetOpen}
        title="Reset Patient?"
        description="This will delete all goals, scheduled jobs, and outbox entries. The patient will be returned to PENDING phase. Conversation history is preserved."
        confirmLabel="Reset"
        onConfirm={handleReset}
        onCancel={() => setConfirmResetOpen(false)}
      />
    </>
  );
}
