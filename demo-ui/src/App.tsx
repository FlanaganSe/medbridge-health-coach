import { useCallback, useEffect, useState } from "react";
import * as api from "./api";
import { usePatientState } from "./hooks/usePatientState";
import { ChatPanel } from "./components/ChatPanel";
import { ObservabilityPanel } from "./components/ObservabilityPanel";
import { TopBar } from "./components/TopBar";
import type { DemoPatient } from "./types";

const TENANT_ID = "demo-tenant";

export function App() {
  const [patients, setPatients] = useState<DemoPatient[]>([]);
  const [selectedPatientId, setSelectedPatientId] = useState<string | null>(
    null,
  );
  const [resetKey, setResetKey] = useState(0);

  const refreshPatients = useCallback(async () => {
    try {
      const list = await api.listPatients(TENANT_ID);
      setPatients(list);
      return list;
    } catch {
      // Silently fail — patients list stays as-is
      return patients;
    }
  }, [patients]);

  // Fetch patients on mount
  useEffect(() => {
    void api.listPatients(TENANT_ID).then(setPatients);
  }, []);

  // Auto-select first patient when list loads and nothing is selected,
  // or when selected patient was deleted
  useEffect(() => {
    if (patients.length === 0) return;
    if (
      !selectedPatientId ||
      !patients.some((p) => p.patient_id === selectedPatientId)
    ) {
      setSelectedPatientId(patients[0].patient_id);
    }
  }, [patients, selectedPatientId]);

  const { state, loadState, lastUpdated, refresh } = usePatientState(
    selectedPatientId ?? "",
    TENANT_ID,
  );

  const handlePatientChange = useCallback((id: string) => {
    setSelectedPatientId(id);
  }, []);

  const handlePatientSeeded = useCallback(
    async (patientId: string) => {
      const list = await refreshPatients();
      if (list.some((p) => p.patient_id === patientId)) {
        setSelectedPatientId(patientId);
      }
    },
    [refreshPatients],
  );

  const handlePatientDeleted = useCallback(async () => {
    await refreshPatients();
  }, [refreshPatients]);

  const handleReset = useCallback(() => {
    setResetKey((k) => k + 1);
    void refreshPatients();
  }, [refreshPatients]);

  return (
    <div className="flex h-screen flex-col bg-bg-page">
      <TopBar
        patients={patients}
        selectedPatientId={selectedPatientId ?? ""}
        onPatientChange={handlePatientChange}
        tenantId={TENANT_ID}
        phase={state.phase}
        onPatientSeeded={handlePatientSeeded}
        onPatientDeleted={handlePatientDeleted}
        onStateChanged={refresh}
        onReset={handleReset}
      />
      <div className="flex min-h-0 flex-1">
        {selectedPatientId ? (
          <>
            <ChatPanel
              key={resetKey}
              patientId={selectedPatientId}
              tenantId={TENANT_ID}
              phase={state.phase}
              onStreamComplete={refresh}
            />
            <ObservabilityPanel
              state={state}
              loadState={loadState}
              lastUpdated={lastUpdated}
              onRetry={refresh}
            />
          </>
        ) : (
          <div className="flex flex-1 items-center justify-center text-text-secondary">
            {patients.length === 0
              ? "No patients yet — click New Patient to get started"
              : "Select a patient to begin"}
          </div>
        )}
      </div>
    </div>
  );
}
