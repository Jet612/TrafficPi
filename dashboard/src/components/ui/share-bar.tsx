import { cn } from "@/lib/cn";
import { OUTCOME_COLOR, sharesOf, totalOf, type OutcomeCounts } from "@/lib/data";
import { formatCount } from "@/lib/format";
import { OUTCOMES, OUTCOME_LABEL } from "@/lib/stops";

/**
 * How the vehicles divide between the four outcomes, as one bar. The numbers are in the tiles
 * above it and the words are in its label; a segment says its own in a tooltip. A 2 px gap of the
 * page's own colour separates the segments, and the ends of the bar are round.
 */
export function ShareBar({ counts, className }: { counts: OutcomeCounts; className?: string }) {
  const total = totalOf(counts);
  const shares = sharesOf(counts);
  const label =
    total === 0
      ? "No vehicles judged"
      : OUTCOMES.filter((outcome) => counts[outcome] > 0)
          .map((outcome) => `${OUTCOME_LABEL[outcome]} ${shares[outcome]}%`)
          .join(", ");
  if (total === 0) return <div role="img" aria-label={label} className={cn("h-3 w-full rounded-full bg-sunken", className)} />;
  return (
    <div role="img" aria-label={label} className={cn("flex h-3 w-full gap-0.5", className)}>
      {OUTCOMES.filter((outcome) => counts[outcome] > 0).map((outcome) => (
        <span
          key={outcome}
          title={`${OUTCOME_LABEL[outcome]}: ${formatCount(counts[outcome])} (${shares[outcome]}%)`}
          className="h-full min-w-1 first:rounded-l-full last:rounded-r-full"
          style={{ flex: `${counts[outcome]} 1 0`, background: OUTCOME_COLOR[outcome] }}
        />
      ))}
    </div>
  );
}
