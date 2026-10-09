"use client";

import { StackedChartCard } from "@/components/stacked-chart";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/stat";
import { cn } from "@/lib/cn";
import { classColor, classLabel } from "@/lib/classes";
import { averagePerFinishedDay, classSeries, dayLabel, peak, rangeText, trafficDayRows, trafficHourRows, weekdayName } from "@/lib/data";
import { formatCount, hourRange } from "@/lib/format";
import type { TrafficStats } from "@/lib/types";

const plural = (n: number, one: string) => `${formatCount(n)} ${one}${n === 1 ? "" : "s"}`;
const percentOf = (part: number, whole: number) => (whole === 0 ? 0 : Math.round((part / whole) * 100));

/** The vehicles counted by the counting lines: how many, when, which kinds, which way. */
export function TrafficStatsSection({ stats, busy }: { stats: TrafficStats; busy: boolean }) {
  const days = stats.by_day.length;
  const busiest = peak(stats.by_hour, (row) => row.total);
  const series = classSeries(stats.by_hour);
  return (
    <>
      <Counted stats={stats} busy={busy} />
      <StackedChartCard
        title="Counted by hour"
        subtitle={busiest ? `Busiest hour ${hourRange(busiest.item.hour)} with ${plural(busiest.value, "vehicle")}` : undefined}
        rows={trafficHourRows(stats.by_hour)}
        series={series}
        unit="vehicle"
        interval={2}
        emptyText="Nothing was counted in this time"
        firstColumn="Hour"
        summary={`Vehicles counted by the hour of the day over ${plural(days, "day")}, stacked by kind.${busiest ? ` The busiest hour is ${hourRange(busiest.item.hour)} with ${busiest.value}.` : " Nothing was counted."}`}
        busy={busy}
        testId="data-traffic-by-hour"
      />
      {days > 1 && (
        <StackedChartCard
          title="Counted day by day"
          subtitle="Today is the last column, and it is not over yet."
          rows={trafficDayRows(stats.by_day)}
          series={series}
          unit="vehicle"
          interval={Math.max(0, Math.ceil(days / 10) - 1)}
          emptyText="Nothing was counted in this time"
          firstColumn="Day"
          summary={`Vehicles counted on each of ${days} days, stacked by kind.`}
          busy={busy}
          testId="data-traffic-by-day"
        />
      )}
      {days >= 14 && <Weekdays stats={stats} busy={busy} />}
    </>
  );
}

function Counted({ stats, busy }: { stats: TrafficStats; busy: boolean }) {
  const busiestHour = peak(stats.by_hour, (row) => row.total);
  const days = stats.by_day.length;
  const busiestDay = days > 1 ? peak(stats.by_day, (row) => row.total) : null;
  const average = averagePerFinishedDay(stats.by_day);
  const kinds = Object.entries(stats.by_class);
  const largest = Math.max(1, ...kinds.map(([, n]) => n));
  const first = stats.by_day[0]?.date;
  const last = stats.by_day.at(-1)?.date;
  return (
    <Card aria-busy={busy} data-testid="data-traffic-counted">
      <CardHeader>
        <div>
          <CardTitle as="h3">Counted</CardTitle>
          <p className="mt-0.5 text-sm text-ink-2">
            Vehicles that crossed a counting line, {stats.all_time ? `since ${rangeText(first, first)}` : rangeText(first, last)}.
          </p>
        </div>
      </CardHeader>
      <CardBody className={cn("grid gap-5 transition-opacity", busy && "opacity-60")}>
        <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile label="Vehicles counted" value={<span data-testid="data-traffic-total">{formatCount(stats.total)}</span>} />
          <StatTile
            label="Average a day"
            value={average === null ? "–" : formatCount(average)}
            note={average === null ? "needs a day that is over" : "over the days that are over"}
          />
          <StatTile
            label="Busiest hour"
            wrap
            value={busiestHour ? hourRange(busiestHour.item.hour) : "–"}
            note={busiestHour ? plural(busiestHour.value, "vehicle") : "nothing counted yet"}
          />
          {busiestDay && <StatTile label="Busiest day" value={dayLabel(busiestDay.item.date)} note={plural(busiestDay.value, "vehicle")} />}
        </dl>

        {kinds.length > 0 && (
          <ul className="flex flex-col gap-2.5" aria-label="Counted by kind of vehicle">
            {kinds.map(([name, count]) => (
              <li key={name} className="grid grid-cols-[6.5rem_1fr_4.5rem_3rem] items-center gap-3 text-sm">
                <span className="truncate text-ink-2">{classLabel(name)}</span>
                <span className="flex h-4 items-center">
                  <span className="h-2.5 rounded-r-[4px]" style={{ width: `${Math.max(2, (count / largest) * 100)}%`, background: classColor(name) }} aria-hidden="true" />
                </span>
                <span className="text-right font-medium tabular-nums text-ink">{formatCount(count)}</span>
                <span className="text-right tabular-nums text-ink-2">{percentOf(count, stats.total)}%</span>
              </li>
            ))}
          </ul>
        )}

        {stats.lines.length > 0 && (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">Crossings by counting line</caption>
              <thead>
                <tr className="text-left text-ink-2">
                  <th scope="col" className="pb-1.5 pr-3 font-medium">Line</th>
                  <th scope="col" className="pb-1.5 pr-3 text-right font-medium" title="Crossing from the left of the line's direction to its right: the way the arrow on the video points">Forward</th>
                  <th scope="col" className="pb-1.5 pr-3 text-right font-medium">Reverse</th>
                  <th scope="col" className="pb-1.5 text-right font-medium">Both ways</th>
                </tr>
              </thead>
              <tbody>
                {stats.lines.map((line) => (
                  <tr key={line.id} className="border-t border-grid">
                    <th scope="row" className="py-1.5 pr-3 text-left font-normal text-ink">{line.name || line.id}</th>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-ink">{formatCount(line.forward)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums text-ink">{formatCount(line.reverse)}</td>
                    <td className="py-1.5 text-right tabular-nums text-ink">{formatCount(line.forward + line.reverse)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <p className="mt-2 text-sm text-ink-3">Forward is the way the arrow on the video points.</p>
            {stats.removed > 0 && (
              <p className="mt-1 text-sm text-ink-3">
                {stats.removed === 1
                  ? "1 more was counted by a line that has since been removed."
                  : `${formatCount(stats.removed)} more were counted by lines that have since been removed.`}
              </p>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/** Which days of the week are busy: one bar for each, in the one colour of a single measure. */
function Weekdays({ stats, busy }: { stats: TrafficStats; busy: boolean }) {
  const largest = Math.max(1, ...stats.by_weekday.map((row) => row.total));
  const busiest = peak(stats.by_weekday, (row) => row.total);
  return (
    <Card data-testid="data-traffic-by-weekday" aria-busy={busy}>
      <CardHeader>
        <div>
          <CardTitle as="h3">Counted by day of the week</CardTitle>
          {busiest && <p className="mt-0.5 text-sm text-ink-2">Busiest: {weekdayName(busiest.item.weekday)}, with {plural(busiest.value, "vehicle")}</p>}
        </div>
      </CardHeader>
      <CardBody className={cn("transition-opacity", busy && "opacity-60")}>
        <ul className="flex flex-col gap-2.5" aria-label="Vehicles counted on each day of the week">
          {stats.by_weekday.map((row) => (
            <li key={row.weekday} className="grid grid-cols-[6.5rem_1fr_4.5rem] items-center gap-3 text-sm">
              <span className="truncate text-ink-2">{weekdayName(row.weekday)}</span>
              <span className="flex h-4 items-center">
                <span className="h-2.5 rounded-r-[4px] bg-accent" style={{ width: `${row.total === 0 ? 0 : Math.max(2, (row.total / largest) * 100)}%` }} aria-hidden="true" />
              </span>
              <span className="text-right font-medium tabular-nums text-ink">{formatCount(row.total)}</span>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}
