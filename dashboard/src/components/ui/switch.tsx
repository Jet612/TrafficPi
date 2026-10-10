import { useId } from "react";
import { InfoTip } from "@/components/ui/info-tip";
import { cn } from "@/lib/cn";

/** A labelled on/off control. The label is part of the target, and both work from the keyboard. */
export function Switch({
  checked,
  onChange,
  label,
  hint,
  className,
  disabled = false,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  label: string;
  /** What it does, in a bubble that an (i) beside the label opens, which the switch is also described by. */
  hint?: React.ReactNode;
  className?: string;
  /** Shown as it is but cannot be changed (something else decides whether it matters). */
  disabled?: boolean;
}) {
  const id = useId();
  const hintId = useId();
  const control = (
    <label
      htmlFor={id}
      className={cn(
        "inline-flex min-h-8 select-none items-center gap-2 text-sm text-ink-2 pointer-coarse:min-h-10",
        disabled ? "cursor-not-allowed opacity-60" : "cursor-pointer",
        className,
      )}
    >
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-describedby={hint ? hintId : undefined}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative h-5 w-9 shrink-0 rounded-full border transition-colors",
          checked ? "border-accent bg-accent" : "border-ink-3/50 bg-baseline",
        )}
      >
        {/* White on the blue track in both themes (the accent's own ink is near black in the dark one and looked like a hole); grey on the off track. */}
        <span
          aria-hidden="true"
          className={cn(
            "absolute top-1/2 size-3.5 -translate-y-1/2 rounded-full transition-[left]",
            checked ? "left-[calc(100%-1.125rem)] bg-white" : "left-0.5 bg-ink-3",
          )}
        />
      </button>
      {label}
    </label>
  );
  if (!hint) return control;
  return (
    <span className="inline-flex items-center gap-1">
      {control}
      <InfoTip id={hintId}>{hint}</InfoTip>
    </span>
  );
}
