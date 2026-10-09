import { useId } from "react";
import { cn } from "@/lib/cn";

/** A labelled on/off control. The label is part of the target, and both work from the keyboard. */
export function Switch({
  checked,
  onChange,
  label,
  className,
  disabled = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  className?: string;
  /** Shown as it is but cannot be changed (something else decides whether it matters). */
  disabled?: boolean;
}) {
  const id = useId();
  return (
    <label
      htmlFor={id}
      className={cn("inline-flex min-h-8 select-none items-center gap-2 text-sm text-ink-2", disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer", className)}
    >
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-5 w-9 shrink-0 rounded-full border transition-colors",
          checked ? "border-accent bg-accent" : "border-baseline bg-sunken",
        )}
      >
        <span
          aria-hidden="true"
          className={cn(
            "absolute top-1/2 size-3.5 -translate-y-1/2 rounded-full transition-[left]",
            checked ? "left-[calc(100%-1.125rem)] bg-accent-ink" : "left-0.5 bg-ink-3",
          )}
        />
      </button>
      {label}
    </label>
  );
}
