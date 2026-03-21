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
        "inline-flex h-9 items-center justify-center gap-1.5 rounded-[6px] px-4 text-[13px] font-medium transition-opacity",
        isDisabled && "cursor-not-allowed opacity-50",
        variant === "primary" && [
          "bg-teal text-white",
          !isDisabled && "hover:opacity-90",
        ],
        variant === "secondary" && !danger && [
          "border border-border-primary bg-bg-card text-text-primary",
          !isDisabled && "hover:bg-bg-muted",
        ],
        variant === "secondary" && danger && [
          "bg-red-light text-red",
          !isDisabled && "hover:opacity-90",
        ],
      )}
    >
      {loading ? (
        <svg
          className="h-3.5 w-3.5 animate-spin"
          viewBox="0 0 24 24"
          fill="none"
          aria-label="Loading"
        >
          <circle
            className="opacity-25"
            cx="12"
            cy="12"
            r="10"
            stroke="currentColor"
            strokeWidth="4"
          />
          <path
            className="opacity-75"
            fill="currentColor"
            d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"
          />
        </svg>
      ) : (
        Icon && (
          <Icon
            size={14}
            className={clsx(
              variant === "secondary" && !danger && "text-text-tertiary",
              variant === "secondary" && danger && "text-red",
            )}
          />
        )
      )}
      {label}
    </button>
  );
}
