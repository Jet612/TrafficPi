import { Check } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * An on/off choice shown as a chip: for a row of several (what to draw over a picture, which kinds to
 * show). On, it has the wash and edge that every selected thing has, and a check; off, a plain edge.
 * It is a switch for a screen reader, and the box for the check is always there so that the chip does
 * not change its width when it is turned on.
 */
export function ToggleChip({
  checked,
  onChange,
  disabled = false,
  className,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      disabled={disabled}
      onClick={() => onChange(!checked)}
      className={cn(
        "inline-flex h-8 select-none items-center gap-1.5 rounded-lg border pl-2 pr-3 text-sm font-medium transition-colors pointer-coarse:h-10 disabled:cursor-not-allowed disabled:opacity-60",
        checked ? "border-accent bg-accent-wash text-ink" : "border-edge bg-surface text-ink-2 hover:bg-sunken hover:text-ink",
        className,
      )}
    >
      <span className="grid size-4 shrink-0 place-items-center" aria-hidden="true">
        {checked && <Check className="size-4" />}
      </span>
      {children}
    </button>
  );
}
