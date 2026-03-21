import { Check, ChevronDown, User } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

interface PatientSelectorProps {
  patients: { id: string; name: string }[];
  selectedId: string;
  onChange: (id: string) => void;
}

export function PatientSelector({
  patients,
  selectedId,
  onChange,
}: PatientSelectorProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  const selected = patients.find((p) => p.id === selectedId);

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
        <span>{selected?.name ?? "Select patient"}</span>
        <ChevronDown size={14} className="text-text-muted" />
      </button>

      {open && (
        <ul
          role="listbox"
          className="absolute right-0 top-full z-50 mt-1 min-w-[260px] rounded-lg border border-border-primary bg-bg-card py-1 shadow-lg"
        >
          {patients.map((p) => (
            <li key={p.id} role="option" aria-selected={p.id === selectedId}>
              <button
                type="button"
                onClick={() => handleSelect(p.id)}
                className="flex w-full items-center gap-2 px-3.5 py-2.5 text-left text-[13px] text-text-primary hover:bg-bg-muted"
              >
                <span className="flex-1">{p.name}</span>
                {p.id === selectedId && (
                  <Check size={14} className="text-teal" />
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
