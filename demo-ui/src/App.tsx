import { useState, useCallback } from "react";
import { Chat } from "./components/Chat";
import { DemoControls } from "./components/DemoControls";
import { ObservabilitySidebar } from "./components/ObservabilitySidebar";

const DEMO_PATIENTS = [
  { id: "00000000-0000-0000-0000-000000000001", name: "Demo Patient 1" },
  { id: "00000000-0000-0000-0000-000000000002", name: "Demo Patient 2" },
];

export function App() {
  const [patientId, setPatientId] = useState(DEMO_PATIENTS[0].id);
  const tenantId = "demo-tenant";

  // Track the internal DB UUID (returned from seed-patient)
  const [internalId, setInternalId] = useState<string | null>(null);

  const handlePatientSeeded = useCallback((id: string) => {
    setInternalId(id);
  }, []);

  // Use internal ID for demo API calls if available, external ID otherwise
  const effectivePatientId = internalId ?? patientId;

  return (
    <div style={{ display: "flex", height: "100vh", fontFamily: "system-ui" }}>
      <div style={{ flex: 1, display: "flex", flexDirection: "column" }}>
        <header
          style={{
            padding: "12px 16px",
            borderBottom: "1px solid #e5e7eb",
            display: "flex",
            alignItems: "center",
            gap: 12,
          }}
        >
          <h1 style={{ margin: 0, fontSize: 18 }}>Health Coach Demo</h1>
          <select
            value={patientId}
            onChange={(e) => {
              setPatientId(e.target.value);
              setInternalId(null);
            }}
            style={{ padding: "4px 8px" }}
          >
            {DEMO_PATIENTS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
          {internalId && (
            <span
              style={{ fontSize: 11, color: "#6b7280", fontFamily: "monospace" }}
            >
              DB: {internalId.slice(0, 8)}...
            </span>
          )}
        </header>
        <DemoControls
          patientId={effectivePatientId}
          tenantId={tenantId}
          onPatientSeeded={handlePatientSeeded}
        />
        <Chat patientId={patientId} tenantId={tenantId} />
      </div>
      <ObservabilitySidebar
        patientId={effectivePatientId}
        tenantId={tenantId}
      />
    </div>
  );
}
