import { cn } from "@/lib/cn";

/** A label above a number, with an optional note below. Use inside a <dl>. */
export function StatTile({
  label,
  value,
  note,
  className,
  children,
}: {
  label: string;
  value: React.ReactNode;
  note?: React.ReactNode;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <div className={cn("min-w-0 rounded-xl border border-edge bg-surface p-4", className)}>
      <dt className="text-sm text-ink-2">{label}</dt>
      <dd className="mt-1">
        <span className="block truncate text-2xl font-semibold leading-8 text-ink">{value}</span>
        {note && <span className="mt-0.5 block text-sm text-ink-2">{note}</span>}
        {children && <span className="mt-3 block">{children}</span>}
      </dd>
    </div>
  );
}
