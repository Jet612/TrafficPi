"use client";

import Link from "next/link";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { useStats } from "@/components/stats-provider";
import { classColor, classLabel, orderedClasses } from "@/lib/classes";
import { formatCount } from "@/lib/format";
import type { TodaySummary } from "@/lib/types";

export function TodayCard() {
  const { today, loaded, failed } = useStats();
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle>Counted today</CardTitle>
        {today && <span className="text-sm text-ink-2">since midnight, {today.timezone.replace("_", " ")}</span>}
      </CardHeader>
      <CardBody className="flex flex-1 flex-col gap-6">
        {!loaded && <p className="text-sm text-ink-2">Loading&hellip;</p>}
        {failed && <p className="text-sm text-ink">Today&rsquo;s numbers could not be loaded. Trying again.</p>}
        {today && <TodayBody today={today} />}
      </CardBody>
    </Card>
  );
}

function TodayBody({ today }: { today: TodaySummary }) {
  const classes = orderedClasses(Object.keys(today.by_class));
  const largest = Math.max(1, ...Object.values(today.by_class));
  return (
    <>
      <div>
        {/* The one number this page leads with. */}
        <p className="text-6xl font-semibold leading-none text-ink" data-testid="today-total">
          {formatCount(today.total)}
        </p>
        <p className="mt-2 text-sm text-ink-2">
          {today.lines.length === 0 ? (
            <>
              {today.total === 0 ? "No counting lines yet." : "All of these were counted by lines that have since been removed."}{" "}
              <Link href="/setup/" className="font-medium text-accent underline underline-offset-2">
                Add a line on the Setup page
              </Link>
              {today.total === 0 ? "." : " to count again."}
            </>
          ) : today.total === 0 ? (
            "Nothing has crossed a counting line yet today."
          ) : (
            `across ${today.lines.length} counting line${today.lines.length === 1 ? "" : "s"}`
          )}
        </p>
      </div>

      {classes.length > 0 && (
        <ul className="flex flex-col gap-2.5" aria-label="Counted today by kind">
          {classes.map((name) => {
            const count = today.by_class[name] ?? 0;
            return (
              <li key={name} className="grid grid-cols-[6.5rem_1fr_3.5rem] items-center gap-3 text-sm">
                <span className="truncate text-ink-2">{classLabel(name)}</span>
                <span className="flex h-4 items-center">
                  <span
                    className="h-2.5 rounded-r-[4px]"
                    style={{ width: `${Math.max(2, (count / largest) * 100)}%`, background: classColor(name) }}
                    aria-hidden="true"
                  />
                </span>
                <span className="text-right font-medium tabular-nums text-ink">{formatCount(count)}</span>
              </li>
            );
          })}
        </ul>
      )}

      {today.lines.length > 0 && (
        <div>
          <table className="w-full text-sm">
            <caption className="sr-only">Crossings by counting line</caption>
            <thead>
              <tr className="text-left text-ink-2">
                <th scope="col" className="pb-1.5 pr-2 font-medium">Line</th>
                <th scope="col" className="pb-1.5 pr-2 text-right font-medium" title="Crossing from the left of the line's direction to its right: the way the arrow on the video points">Forward</th>
                <th scope="col" className="pb-1.5 text-right font-medium">Reverse</th>
              </tr>
            </thead>
            <tbody>
              {today.lines.map((line) => (
                <tr key={line.id} className="border-t border-grid">
                  <th scope="row" className="py-1.5 pr-2 text-left font-normal text-ink">{line.name || line.id}</th>
                  <td className="py-1.5 pr-2 text-right tabular-nums text-ink">{formatCount(line.forward)}</td>
                  <td className="py-1.5 text-right tabular-nums text-ink">{formatCount(line.reverse)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-2 text-sm text-ink-3">Forward is the way the arrow on the video points.</p>
          {today.removed > 0 && (
            <p className="mt-1 text-sm text-ink-3" data-testid="removed-lines-note">
              {today.removed === 1
                ? "1 more was counted by a line that has since been removed."
                : `${formatCount(today.removed)} more were counted by lines that have since been removed.`}
            </p>
          )}
        </div>
      )}
    </>
  );
}
