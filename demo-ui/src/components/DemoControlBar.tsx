import { FlaskConical, RefreshCw, RotateCcw, UserPlus, Zap } from "lucide-react";
import { useCallback, useState } from "react";
import * as api from "../api";
import type { ResetPatientResponse, TriggerFollowupResponse } from "../types";
import { Button } from "./ui/Button";
import { ConfirmDialog } from "./ui/ConfirmDialog";

type Status = "idle" | "loading" | "success" | "error";

interface DemoControlBarProps {
  patientId: string;
  externalPatientId: string;
  tenantId: string;
  onPatientSeeded: (id: string) => void;
  onStateChanged: () => void;
}

export function DemoControlBar({
  patientId,
  externalPatientId,
  tenantId,
  onPatientSeeded,
  onStateChanged,
}: DemoControlBarProps) {
  const [seedStatus, setSeedStatus] = useState<Status>("idle");
  const [triggerStatus, setTriggerStatus] = useState<Status>("idle");
  const [resetStatus, setResetStatus] = useState<Status>("idle");
  const [statusMessage, setStatusMessage] = useState("");
  const [confirmResetOpen, setConfirmResetOpen] = useState(false);

  const showStatus = useCallback((msg: string) => {
    setStatusMessage(msg);
    setTimeout(() => setStatusMessage(""), 4000);
  }, []);

  const handleSeed = useCallback(async () => {
    setSeedStatus("loading");
    try {
      const data = await api.seedPatient(tenantId, externalPatientId);
      onPatientSeeded(data.patient_id);
      setSeedStatus("success");
      showStatus(`Patient seeded (${data.phase})`);
      onStateChanged();
    } catch (err) {
      setSeedStatus("error");
      showStatus(`Seed failed: ${err}`);
    }
  }, [tenantId, externalPatientId, onPatientSeeded, showStatus, onStateChanged]);

  const handleTrigger = useCallback(async () => {
    setTriggerStatus("loading");
    try {
      const data: TriggerFollowupResponse =
        await api.triggerFollowup(patientId);
      setTriggerStatus("success");
      showStatus(`Expedited ${data.job_type}`);
      onStateChanged();
    } catch (err) {
      setTriggerStatus("error");
      showStatus(`No pending check-ins: ${err}`);
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
      );
      onStateChanged();
    } catch (err) {
      setResetStatus("error");
      showStatus(`Reset failed: ${err}`);
    }
  }, [patientId, showStatus, onStateChanged]);

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
        <div className="border-b border-border bg-amber-badge-bg px-6 py-2 text-[12px] text-amber-badge-text">
          {statusMessage}
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
