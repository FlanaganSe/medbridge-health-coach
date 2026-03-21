import { useCallback, useState } from "react";
import { usePatientState } from "./hooks/usePatientState";
import { ChatPanel } from "./components/ChatPanel";
import { ObservabilityPanel } from "./components/ObservabilityPanel";
import { TopBar } from "./components/TopBar";

const DEMO_PATIENTS = [
  { id: "00000000-0000-0000-0000-000000000001", name: "Sarah M. — Knee Rehab" },
  { id: "00000000-0000-0000-0000-000000000002", name: "James T. — Shoulder Recovery" },
];

const TENANT_ID = "demo-tenant";

export function App() {
  const [externalPatientId, setExternalPatientId] = useState(
    DEMO_PATIENTS[0].id,
  );
  const [internalId, setInternalId] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);

  const effectivePatientId = internalId ?? externalPatientId;

  const { state, loadState, lastUpdated, refresh } = usePatientState(
    effectivePatientId,
    TENANT_ID,
  );

  const handlePatientChange = useCallback((id: string) => {
    setExternalPatientId(id);
    setInternalId(null);
  }, []);

  const handlePatientSeeded = useCallback((id: string) => {
    setInternalId(id);
  }, []);

  const handleReset = useCallback(() => {
    setResetKey((k) => k + 1);
  }, []);

  return (
    <div className="flex h-screen flex-col bg-bg-page">
      <TopBar
        patients={DEMO_PATIENTS}
        selectedPatientId={externalPatientId}
        onPatientChange={handlePatientChange}
        patientId={effectivePatientId}
        externalPatientId={externalPatientId}
        tenantId={TENANT_ID}
        onPatientSeeded={handlePatientSeeded}
        onStateChanged={refresh}
        onReset={handleReset}
      />
      <div className="flex min-h-0 flex-1">
        <ChatPanel
          key={resetKey}
          patientId={effectivePatientId}
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
      </div>
    </div>
  );
}
