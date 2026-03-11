import { useState } from "react";
import { Chat } from "./components/Chat";
import { ObservabilitySidebar } from "./components/ObservabilitySidebar";

const DEMO_PATIENTS = [
  { id: "00000000-0000-0000-0000-000000000001", name: "Demo Patient 1" },
  { id: "00000000-0000-0000-0000-000000000002", name: "Demo Patient 2" },
];

export function App() {
  const [patientId, setPatientId] = useState(DEMO_PATIENTS[0].id);
  const tenantId = "demo-tenant";

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
            onChange={(e) => setPatientId(e.target.value)}
            style={{ padding: "4px 8px" }}
          >
            {DEMO_PATIENTS.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </header>
        <Chat patientId={patientId} tenantId={tenantId} />
      </div>
      <ObservabilitySidebar patientId={patientId} tenantId={tenantId} />
    </div>
  );
}
