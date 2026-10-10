"use client";

import { ClassSplit } from "@/components/class-split";
import { StackedChartCard } from "@/components/stacked-chart";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/stat";
import { cn } from "@/lib/cn";
import { averagePerFinishedDay, classSeries, dayLabel, daysWithData, peak, trafficDayRows, trafficHourRows, weekdayName } from "@/lib/data";
import { formatCount, hourRange } from "@/lib/format";
import type { TrafficStats } from "@/lib/types";

const plural = (n: number, one: string) => `${formatCount(n)} ${one}${n === 1 ? "" : "s"}`;

/** The vehicles counted by the counting lines: how many, when, which kinds, which way. */
export function TrafficStatsSection({ stats, busy }: { stats: TrafficStats; busy: boolean }) {
  const days = stats.by_day.length;
  const busiest = peak(stats.by_hour, (row) => row.total);
  const series = classSeries(stats.by_hour);
  return (
    <>
      <Counted stats={stats} busy={busy} />
      <StackedChartCard
        title="Vehicles counted by hour"
        subtitle={busiest ? `Busiest hour ${hourRange(busiest.item.hour)} · ${plural(busiest.value, "vehicle")}` : undefined}
        rows={trafficHourRows(stats.by_hour)}
        series={series}
        unit="vehicle"
        kinds
        interval={2}
        emptyText="Nothing was counted in this time"
        firstColumn="Hour"
        summary={`Vehicles counted by the hour of the day over ${plural(days, "day")}, stacked by kind.${busiest ? ` The busiest hour is ${hourRange(busiest.item.hour)} with ${busiest.value}.` : " Nothing was counted."}`}
        busy={busy}
        testId="data-traffic-by-hour"
      />
      {days > 1 && (
        <StackedChartCard
          title="Vehicles counted by day"
          subtitle="Today is the last column, and it is not over yet."
          rows={trafficDayRows(stats.by_day)}
          series={series}
          unit="vehicle"
          kinds
          startAtData
          maxBar={64}
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
  // A busiest day means something from two days that have anything in them, not from a period whose other days are empty.
  const busiestDay = days > 1 && daysWithData(stats.by_day) > 1 ? peak(stats.by_day, (row) => row.total) : null;
  const average = averagePerFinishedDay(stats.by_day);
  return (
    <Card aria-busy={busy} data-testid="data-traffic-counted">
      <CardHeader>
        <div>
          <CardTitle as="h3">Counted</CardTitle>
          <CardSubtitle>Vehicles that crossed a counting line.</CardSubtitle>
        </div>
      </CardHeader>
      <CardBody className={cn("grid gap-5 transition-opacity", busy && "opacity-60")}>
        <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <StatTile size="tile" label="Vehicles counted" value={<span data-testid="data-traffic-total">{formatCount(stats.total)}</span>} />
          <StatTile
            size="tile"
            label="Average a day"
            value={average === null ? <span className="text-xl text-ink-3">Not enough data yet</span> : formatCount(average)}
            note={average === null ? undefined : "over the days that are over"}
            wrap
          />
          <StatTile
            size="tile"
            label="Busiest hour (all traffic)"
            wrap
            value={busiestHour ? hourRange(busiestHour.item.hour) : "–"}
            note={busiestHour ? plural(busiestHour.value, "vehicle") : "nothing counted yet"}
          />
          {busiestDay && <StatTile size="tile" label="Busiest day" value={dayLabel(busiestDay.item.date)} note={plural(busiestDay.value, "vehicle")} wrap />}
        </dl>

        {/* The kinds and the lines side by side, each as wide as it needs: names and numbers a thousand pixels apart cannot be read across. */}
        <div className="grid gap-x-10 gap-y-6 lg:grid-cols-2">
          <ClassSplit counts={stats.by_class} label="Counted by kind of vehicle" />
          {stats.lines.length > 0 && (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <caption className="sr-only">Crossings by counting line</caption>
                <thead>
                  <tr className="text-left text-ink-2">
                    <th scope="col" className="pb-1.5 pr-3 font-medium">Line</th>
                    <th scope="col" className="whitespace-nowrap pb-1.5 pr-3 text-right font-medium">Forward</th>
                    <th scope="col" className="whitespace-nowrap pb-1.5 pr-3 text-right font-medium">Reverse</th>
                    <th scope="col" className="whitespace-nowrap pb-1.5 text-right font-medium">Both ways</th>
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
                  {stats.removed > 0 && (
                    <tr className="border-t border-grid text-ink-3" data-testid="data-removed-lines">
                      <th scope="row" className="py-1.5 pr-3 text-left font-normal">Removed lines</th>
                      <td className="py-1.5 pr-3 text-right">–</td>
                      <td className="py-1.5 pr-3 text-right">–</td>
                      <td className="py-1.5 text-right tabular-nums">{formatCount(stats.removed)}</td>
                    </tr>
                  )}
                </tbody>
              </table>
              <p className="mt-2 text-sm text-ink-3">Forward is the way the arrow on the video points.</p>
            </div>
          )}
        </div>
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
