import { useCallback, useEffect, useRef, useState } from "react";

interface CreatePatientDialogProps {
  open: boolean;
  loading: boolean;
  onConfirm: (displayName: string) => void;
  onCancel: () => void;
}

export function CreatePatientDialog({
  open,
  loading,
  onConfirm,
  onCancel,
}: CreatePatientDialogProps) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState("");

  useEffect(() => {
    if (open) {
      setName("");
      // Small delay to ensure the dialog is rendered before focusing
      requestAnimationFrame(() => inputRef.current?.focus());
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onCancel]);

  const handleSubmit = useCallback(
    (e: React.FormEvent<HTMLFormElement>) => {
      e.preventDefault();
      const trimmed = name.trim();
      if (trimmed) onConfirm(trimmed);
    },
    [name, onConfirm],
  );

  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onCancel}
      role="presentation"
    >
      <div
        className="w-full max-w-sm rounded-xl bg-bg-card p-6 shadow-lg"
        onClick={(e) => e.stopPropagation()}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-patient-title"
      >
        <h3
          id="create-patient-title"
          className="font-heading text-[15px] font-semibold text-text-primary"
        >
          New Patient
        </h3>
        <p className="mt-1 text-[12px] text-text-secondary">
          Create a new demo patient. They will start in the PENDING phase.
        </p>
        <form onSubmit={handleSubmit}>
          <label
            htmlFor="patient-name"
            className="mt-4 block text-[12px] font-medium text-text-secondary"
          >
            Display Name
          </label>
          <input
            ref={inputRef}
            id="patient-name"
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="e.g. Alice W. — Hip Recovery"
            className="mt-1 w-full rounded-[6px] border border-border-primary bg-bg-muted px-3 py-2 text-[13px] text-text-primary placeholder:text-text-muted focus:border-teal focus:outline-none"
            maxLength={200}
            required
            disabled={loading}
          />
          <div className="mt-5 flex justify-end gap-3">
            <button
              type="button"
              onClick={onCancel}
              disabled={loading}
              className="rounded-[6px] border border-border-primary bg-bg-card px-4 py-2 text-[13px] font-medium text-text-primary hover:bg-bg-muted disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={loading || !name.trim()}
              className="rounded-[6px] bg-teal px-4 py-2 text-[13px] font-medium text-white hover:opacity-90 disabled:opacity-50"
            >
              {loading ? "Creating..." : "Create"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
