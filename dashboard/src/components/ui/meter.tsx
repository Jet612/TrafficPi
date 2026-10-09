import { cn } from "@/lib/cn";
import type { Severity } from "@/lib/severity";

const FILL: Record<Severity, string> = {
  ok: "bg-accent",
  warning: "bg-warning",
  critical: "bg-critical",
};

// The unfilled track is a lighter step of the fill's own hue, so the state reads along the bar.
const TRACK: Record<Severity, string> = {
  ok: "bg-accent-wash",
  warning: "bg-[color-mix(in_oklab,var(--warning)_28%,var(--surface))]",
  critical: "bg-[color-mix(in_oklab,var(--critical)_24%,var(--surface))]",
};

/** A reading against a limit. The fill turns from accent to warning to critical as it gets serious. */
export function Meter({
  value,
  min = 0,
  max = 100,
  severity = "ok",
  label,
  valueText,
  className,
}: {
  value: number;
  min?: number;
  max?: number;
  severity?: Severity;
  label: string;
  valueText?: string;
  className?: string;
}) {
  const fraction = max > min ? Math.min(1, Math.max(0, (value - min) / (max - min))) : 0;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-valuetext={valueText}
      className={cn("h-2 w-full overflow-hidden rounded-full", TRACK[severity], className)}
    >
      <div className={cn("h-full rounded-full", FILL[severity])} style={{ width: `${fraction * 100}%` }} />
    </div>
  );
}
