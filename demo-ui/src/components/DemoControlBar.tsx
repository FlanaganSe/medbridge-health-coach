import { clsx } from "clsx";
import { FlaskConical, RefreshCw, RotateCcw, UserPlus, Zap } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api";
import type { ResetPatientResponse, TriggerFollowupResponse } from "../types";
import { Button } from "./ui/Button";
import { ConfirmDialog } from "./ui/ConfirmDialog";

type Status = "idle" | "loading" | "success" | "error";

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

interface DemoControlBarProps {
  patientId: string;
  externalPatientId: string;
  tenantId: string;
  onPatientSeeded: (id: string) => void;
  onStateChanged: () => void;
  onReset: () => void;
}

export function DemoControlBar({
  patientId,
  externalPatientId,
  tenantId,
  onPatientSeeded,
  onStateChanged,
  onReset,
}: DemoControlBarProps) {
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

  const handleRefreshJobs = useCallback(() => {
    onStateChanged();
  }, [onStateChanged]);

  return (
    <>
      <div className="flex shrink-0 items-center justify-between border-b border-border bg-bg-faint px-6 py-2.5">
        {/* Left: label */}
        <div className="flex items-center gap-4">
          <FlaskConical size={14} className="text-brand-red" />
          <span className="text-[13px] font-semibold text-text-primary">
            Demo Controls
          </span>
        </div>

        {/* Right: action buttons */}
        <div className="flex items-center gap-2">
          <Button
            label="Seed Patient"
            icon={UserPlus}
            loading={seedStatus === "loading"}
            onClick={handleSeed}
          />
          <Button
            label="Run Next Check-in"
            icon={Zap}
            loading={triggerStatus === "loading"}
            onClick={handleTrigger}
          />
          <Button
            label="Reset Patient"
            icon={RotateCcw}
            danger
            loading={resetStatus === "loading"}
            onClick={() => setConfirmResetOpen(true)}
          />
          <Button
            label="Refresh"
            icon={RefreshCw}
            onClick={handleRefreshJobs}
          />
        </div>
      </div>

      {/* Status message */}
      {statusMessage && (
        <div
          className={clsx(
            "border-b border-border px-6 py-2 text-[12px]",
            statusMessage.type === "success"
              ? "bg-green-badge-bg text-green-badge-text"
              : "bg-red-badge-bg text-red-badge-text",
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
