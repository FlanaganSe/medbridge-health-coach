import { HeartPulse, Plus, RefreshCw, RotateCcw, Zap } from "lucide-react";
import { clsx } from "clsx";
import { useCallback, useEffect, useRef, useState } from "react";
import * as api from "../api";
import type { DemoPatient, Phase, ResetPatientResponse } from "../types";
import { Button } from "./ui/Button";
import { ConfirmDialog } from "./ui/ConfirmDialog";
import { CreatePatientDialog } from "./ui/CreatePatientDialog";
import { PatientSelector } from "./ui/PatientSelector";

type Status = "idle" | "loading" | "success" | "error";

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

interface TopBarProps {
  patients: DemoPatient[];
  selectedPatientId: string;
  onPatientChange: (id: string) => void;
  tenantId: string;
  phase: Phase;
  onPatientSeeded: (id: string) => Promise<void>;
  onPatientDeleted: () => Promise<void>;
  onStateChanged: () => void;
  onReset: () => void;
}

export function TopBar({
  patients,
  selectedPatientId,
  onPatientChange,
  tenantId,
  phase,
  onPatientSeeded,
  onPatientDeleted,
  onStateChanged,
  onReset,
}: TopBarProps) {
  const [checkinStatus, setCheckinStatus] = useState<Status>("idle");
  const [resetStatus, setResetStatus] = useState<Status>("idle");
  const [createStatus, setCreateStatus] = useState<Status>("idle");
  const [statusMessage, setStatusMessage] = useState<{
    text: string;
    type: "success" | "error";
  } | null>(null);
  const [confirmResetOpen, setConfirmResetOpen] = useState(false);
  const [confirmDeleteId, setConfirmDeleteId] = useState<string | null>(null);
  const [createDialogOpen, setCreateDialogOpen] = useState(false);
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

  const handleCheckin = useCallback(async () => {
    setCheckinStatus("loading");
    try {
      const data = await api.runCheckin(selectedPatientId);
      setCheckinStatus("success");
      showStatus(`Check-in complete (phase: ${data.phase})`, "success");
      onStateChanged();
    } catch (err) {
      setCheckinStatus("error");
      showStatus(`Check-in failed: ${errorMessage(err)}`, "error");
    }
  }, [selectedPatientId, showStatus, onStateChanged]);

  const handleReset = useCallback(async () => {
    setConfirmResetOpen(false);
    setResetStatus("loading");
    try {
      const data: ResetPatientResponse = await api.resetPatient(selectedPatientId);
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
  }, [selectedPatientId, showStatus, onStateChanged, onReset]);

  const handleCreatePatient = useCallback(
    async (displayName: string) => {
      setCreateStatus("loading");
      try {
        const data = await api.seedPatient(tenantId, crypto.randomUUID(), displayName);
        setCreateStatus("idle");
        setCreateDialogOpen(false);
        showStatus(`Patient created: ${displayName}`, "success");
        await onPatientSeeded(data.patient_id);
      } catch (err) {
        setCreateStatus("error");
        showStatus(`Create failed: ${errorMessage(err)}`, "error");
      }
    },
    [tenantId, onPatientSeeded, showStatus],
  );

  const handleDeletePatient = useCallback(async () => {
    if (!confirmDeleteId) return;
    const id = confirmDeleteId;
    setConfirmDeleteId(null);
    try {
      await api.deletePatient(id);
      showStatus("Patient deleted", "success");
      await onPatientDeleted();
    } catch (err) {
      showStatus(`Delete failed: ${errorMessage(err)}`, "error");
    }
  }, [confirmDeleteId, onPatientDeleted, showStatus]);

  const checkinDisabled =
    phase === "pending" || phase === "onboarding" || !selectedPatientId;

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
            onDelete={(id) => setConfirmDeleteId(id)}
          />

          <Button
            label="New Patient"
            icon={Plus}
            onClick={() => setCreateDialogOpen(true)}
          />

          <div className="h-6 w-px bg-border-primary" />

          <Button
            label="Run Check-in"
            icon={Zap}
            variant="primary"
            loading={checkinStatus === "loading"}
            disabled={checkinDisabled}
            title={
              checkinDisabled
                ? "Complete onboarding first (confirm a goal)"
                : undefined
            }
            onClick={handleCheckin}
          />
          <Button
            label="Reset"
            icon={RotateCcw}
            danger
            loading={resetStatus === "loading"}
            disabled={!selectedPatientId}
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

      <ConfirmDialog
        open={confirmDeleteId !== null}
        title="Delete Patient?"
        description="This will permanently delete the patient and all associated data (goals, jobs, conversation history). This cannot be undone."
        confirmLabel="Delete"
        onConfirm={handleDeletePatient}
        onCancel={() => setConfirmDeleteId(null)}
      />

      <CreatePatientDialog
        open={createDialogOpen}
        loading={createStatus === "loading"}
        onConfirm={handleCreatePatient}
        onCancel={() => setCreateDialogOpen(false)}
      />
    </>
  );
}
