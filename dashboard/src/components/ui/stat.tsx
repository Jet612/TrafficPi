import { cn } from "@/lib/cn";

/**
 * The three sizes a number is shown in, so that a page does not invent a fourth: the one figure a page
 * leads with (`hero`), a headline figure in a row of tiles (`tile`), and a figure beside others that
 * matter as much or less (`small`).
 */
export const METRIC_SIZE = {
  hero: "text-5xl font-semibold leading-none sm:text-6xl",
  tile: "text-3xl font-semibold leading-9",
  small: "text-2xl font-semibold leading-8",
} as const;

export type MetricSize = keyof typeof METRIC_SIZE;

/**
 * A label above a number, with an optional note below. Use inside a <dl>. A tile sits on the card's
 * own surface, one step darker and without a border: one border level (the card's) is enough.
 */
export function StatTile({
  label,
  value,
  note,
  size = "small",
  wrap = false,
  className,
  children,
}: {
  label: string;
  value: React.ReactNode;
  note?: React.ReactNode;
  size?: MetricSize;
  /** Let a long value break onto a second line, instead of ending in an ellipsis. */
  wrap?: boolean;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn("min-w-0 rounded-xl bg-sunken p-4", className)}>
      <dt className="text-sm text-ink-2">{label}</dt>
      <dd className="mt-1">
        <span className={cn("block text-ink", METRIC_SIZE[size], wrap ? "break-words" : "truncate")}>{value}</span>
        {note && <span className="mt-0.5 block break-words text-sm text-ink-2">{note}</span>}
        {children && <span className="mt-3 block">{children}</span>}
      </dd>
    </div>
  );
}
