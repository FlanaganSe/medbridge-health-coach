interface Patient {
  id: string;
  name: string;
}

interface TopBarProps {
  patients: Patient[];
  selectedPatientId: string;
  internalId: string | null;
  onPatientChange: (id: string) => void;
}

export function TopBar({
  patients,
  selectedPatientId,
  internalId,
  onPatientChange,
}: TopBarProps) {
  return (
    <header className="flex h-14 shrink-0 items-center justify-between border-b border-border bg-white px-6">
      {/* Left: branding */}
      <div className="flex items-center gap-4">
        <div className="flex h-7 w-7 items-center justify-center rounded-md bg-brand-red">
          <span className="text-[13px] font-bold text-white">M</span>
        </div>
        <span className="font-heading text-lg font-semibold text-text-primary">
          MedBridge
        </span>
        <span className="text-sm text-text-secondary">Health Coach</span>
      </div>

      {/* Right: demo label + patient selector */}
      <div className="flex items-center gap-3">
        <span className="text-[11px] font-semibold tracking-wide text-brand-red">
          DEMO MODE
        </span>
        <select
          value={selectedPatientId}
          onChange={(e) => onPatientChange(e.target.value)}
          className="rounded-md border border-border bg-white px-3.5 py-2 text-[13px] text-text-primary outline-none"
        >
          {patients.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>
        {internalId && (
          <span className="font-mono text-[11px] text-text-muted">
            {internalId.slice(0, 8)}...
          </span>
        )}
      </div>
    </header>
  );
}
