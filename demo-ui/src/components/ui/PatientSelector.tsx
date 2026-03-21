import { Check, ChevronDown, Trash2, User } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import type { DemoPatient } from "../../types";

interface PatientSelectorProps {
  patients: DemoPatient[];
  selectedId: string;
  onChange: (id: string) => void;
  onDelete?: (patientId: string) => void;
}

export function PatientSelector({
  patients,
  selectedId,
  onChange,
  onDelete,
}: PatientSelectorProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const selected = patients.find((p) => p.patient_id === selectedId);
  const displayLabel =
    selected?.display_name ?? selected?.external_patient_id ?? "Select patient";

  // Close on click outside
  useEffect(() => {
    if (!open) return;
    function onMouseDown(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onMouseDown);
    return () => document.removeEventListener("mousedown", onMouseDown);
  }, [open]);

  // Close on Escape
  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  const handleSelect = useCallback(
    (id: string) => {
      onChange(id);
      setOpen(false);
    },
    [onChange],
  );

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className="flex items-center gap-2 rounded-lg border border-border-primary bg-bg-card px-3.5 py-2 text-[13px] text-text-primary hover:bg-bg-muted"
      >
        <User size={14} className="text-text-tertiary" />
        <span className="max-w-[200px] truncate">{displayLabel}</span>
        <ChevronDown size={14} className="text-text-muted" />
      </button>

      {open && (
        <ul
          role="listbox"
          className="absolute right-0 top-full z-50 mt-1 min-w-[280px] rounded-lg border border-border-primary bg-bg-card py-1 shadow-lg"
        >
          {patients.length === 0 && (
            <li className="px-3.5 py-2.5 text-[13px] text-text-muted">
              No patients
            </li>
          )}
          {patients.map((p) => {
            const isSelected = p.patient_id === selectedId;
            const label = p.display_name ?? p.external_patient_id;
            return (
              <li
                key={p.patient_id}
                role="option"
                aria-selected={isSelected}
              >
                <div className="flex items-center gap-1 hover:bg-bg-muted">
                  <button
                    type="button"
                    onClick={() => handleSelect(p.patient_id)}
                    className="flex flex-1 items-center gap-2 px-3.5 py-2.5 text-left text-[13px] text-text-primary"
                  >
                    <span className="flex-1 truncate">{label}</span>
                    {isSelected && (
                      <Check size={14} className="shrink-0 text-teal" />
                    )}
                  </button>
                  {onDelete && !isSelected && (
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setOpen(false);
                        onDelete(p.patient_id);
                      }}
                      className="mr-2 shrink-0 rounded p-1 text-text-muted hover:bg-red-light hover:text-red"
                      title="Delete patient"
                    >
                      <Trash2 size={12} />
                    </button>
                  )}
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </div>
  );
}
