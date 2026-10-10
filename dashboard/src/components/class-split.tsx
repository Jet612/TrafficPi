import { ClassIcon } from "@/components/ui/class-icon";
import { classColor, classLabel, orderedClasses } from "@/lib/classes";
import { formatCount, shareText } from "@/lib/format";

/**
 * How the vehicles divide between the kinds: one bar that is the whole, cut in the kinds' colours, and
 * under it a row for each kind with its icon, its count and its share. (A bar for each kind against the
 * biggest one made every kind but cars the same sliver; a share of the whole tells them apart.)
 */
export function ClassSplit({ counts, label, className }: { counts: Readonly<Record<string, number>>; label: string; className?: string }) {
  const names = orderedClasses(Object.keys(counts));
  if (names.length === 0) return null;
  const total = names.reduce((sum, name) => sum + (counts[name] ?? 0), 0);
  const present = names.filter((name) => (counts[name] ?? 0) > 0);
  const summary = present.map((name) => `${classLabel(name)} ${shareText(counts[name] ?? 0, total)}`).join(", ");
  return (
    <div className={className}>
      <div role="img" aria-label={`${label}: ${summary || "none"}`} className="flex h-2.5 w-full gap-0.5">
        {present.length === 0 ? (
          <span className="h-full w-full rounded-full bg-sunken" />
        ) : (
          present.map((name) => (
            <span
              key={name}
              title={`${classLabel(name)}: ${formatCount(counts[name] ?? 0)} (${shareText(counts[name] ?? 0, total)})`}
              className="h-full min-w-[3px] first:rounded-l-full last:rounded-r-full"
              style={{ flex: `${counts[name]} 1 0`, background: classColor(name) }}
            />
          ))
        )}
      </div>
      <ul aria-label={label} className="mt-3 grid gap-1.5">
        {names.map((name) => (
          <li key={name} className="grid grid-cols-[1rem_minmax(0,1fr)_auto_3rem] items-center gap-2.5 text-sm">
            <ClassIcon name={name} />
            <span className="truncate text-ink-2">{classLabel(name)}</span>
            <span className="text-right font-medium tabular-nums text-ink">{formatCount(counts[name] ?? 0)}</span>
            <span className="text-right tabular-nums text-ink-2">{shareText(counts[name] ?? 0, total)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
