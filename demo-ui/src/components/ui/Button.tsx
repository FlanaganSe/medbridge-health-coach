import type { LucideIcon } from "lucide-react";
import { clsx } from "clsx";

interface ButtonProps {
  label: string;
  icon?: LucideIcon;
  variant?: "primary" | "secondary";
  danger?: boolean;
  loading?: boolean;
  disabled?: boolean;
  onClick: () => void;
}

export function Button({
  label,
  icon: Icon,
  variant = "secondary",
  danger = false,
  loading = false,
  disabled = false,
  onClick,
}: ButtonProps) {
  const isDisabled = loading || disabled;

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={isDisabled}
      className={clsx(
        "inline-flex h-9 items-center justify-center gap-2 rounded-md px-4.5 text-[13px] font-medium transition-opacity",
        isDisabled && "cursor-not-allowed opacity-50",
        variant === "primary" && [
          "bg-text-primary text-white",
          !isDisabled && "hover:opacity-90",
        ],
        variant === "secondary" && [
          "border border-border bg-white",
          danger ? "text-red-badge-text" : "text-text-primary",
          !isDisabled && "hover:bg-bg-faint",
        ],
      )}
    >
      {Icon && (
        <Icon
          size={14}
          className={clsx(
            variant === "secondary" &&
              (danger ? "text-red-badge-text" : "text-text-secondary"),
          )}
        />
      )}
      {loading ? "..." : label}
    </button>
  );
}
