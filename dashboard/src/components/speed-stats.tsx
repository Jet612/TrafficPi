"use client";

import Link from "next/link";
import { StackedChartCard } from "@/components/stacked-chart";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/stat";
import { cn } from "@/lib/cn";
import { formatCount, hourRange } from "@/lib/format";
import { MEASURED_CAVEAT, unitName } from "@/lib/speed";
import { SPEED_SERIES, coverageText, speedBinRows, speedValue } from "@/lib/speeds";
import type { SpeedStats } from "@/lib/types";

const plural = (n: number, one: string) => `${formatCount(n)} ${one}${n === 1 ? "" : "s"}`;

/** The counts with a speed from an earlier calibration, which no figure here includes; null when there are none. */
function leftOutText(stats: SpeedStats): string | null {
  if (stats.excluded === 0) return null;
  return `${plural(stats.excluded, "vehicle")} counted with a speed from an earlier calibration ${stats.excluded === 1 ? "is" : "are"} left out.`;
}

/**
 * How fast the vehicles that the counting lines counted were going (plan.md M6): the figures that
 * a speed study is summed up by, a histogram, and the same by counting line and by hour. A speed is
 * given only for a vehicle seen for long enough in a part of the picture the calibration can be
 * believed, so the page says how many of the vehicles counted have one.
 */
export function SpeedStatsSection({ stats, busy }: { stats: SpeedStats; busy: boolean }) {
  const { figures, units } = stats;
  const leftOut = leftOutText(stats);
  if (figures === null) {
    return (
      <Card data-testid="data-speeds-none">
        <CardBody>
          <p className="max-w-2xl py-2 text-sm text-ink-2">
            {stats.counted === 0
              ? "No vehicle was counted in this time, so there is no speed to show."
              : `No speed was measured for the ${plural(stats.counted, "vehicle")} counted in this time.`}{" "}
            A counting line gives a speed once the camera is calibrated, for a vehicle seen for long enough in a part of the picture the calibration reaches.{" "}
            {leftOut !== null && `${leftOut} `}
            <Link href="/setup/" className="font-medium text-accent underline underline-offset-2">
              Calibrate the camera on the Setup page
            </Link>
            .
          </p>
        </CardBody>
      </Card>
    );
  }
  const days = stats.by_day.length;
  const unit = unitName(units);
  return (
    <>
      <Card aria-busy={busy} data-testid="data-speeds">
        <CardHeader>
          <div>
            <CardTitle as="h3">How fast</CardTitle>
            <p className="mt-0.5 text-sm text-ink-2">
              Vehicles that crossed a counting line, in {units === "mph" ? "miles" : "kilometres"} an hour. {coverageText(stats)}
            </p>
          </div>
        </CardHeader>
        <CardBody className={cn("grid gap-4 transition-opacity", busy && "opacity-60")}>
          <dl className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <StatTile label="Average" value={speedValue(figures.mean, units)} note={`over ${plural(figures.count, "vehicle")}`} />
            <StatTile label="Median" value={speedValue(figures.median, units)} note="half went faster" />
            <StatTile
              label="85th percentile"
              value={<span data-testid="data-speeds-p85">{speedValue(figures.p85, units)}</span>}
              note="85 in 100 went no faster"
            />
            <StatTile label="Fastest" value={speedValue(figures.fastest, units)} note={`the slowest was ${speedValue(figures.slowest, units)}`} />
          </dl>
          <p className="text-sm text-ink-3">
            {MEASURED_CAVEAT}
            {leftOut !== null && ` ${leftOut}`}
          </p>
        </CardBody>
      </Card>

      <StackedChartCard
        title="Speeds"
        subtitle={`Vehicles in each ${SPEED_BIN_WORDS[units]} of speed.`}
        rows={speedBinRows(stats.bins, units)}
        series={SPEED_SERIES}
        unit="vehicle"
        interval={Math.max(0, Math.ceil(stats.bins.length / 12) - 1)}
        emptyText="No speed was measured in this time"
        firstColumn={`From (${unit})`}
        summary={`How many vehicles went at each speed, in bars ${SPEED_BIN_WORDS[units]} wide. The average was ${speedValue(figures.mean, units)} and 85 in 100 went no faster than ${speedValue(figures.p85, units)}.`}
        busy={busy}
        testId="data-speeds-histogram"
      />

      <div className="grid gap-4 lg:grid-cols-2">
        <ByLine stats={stats} busy={busy} />
        <ByHour stats={stats} busy={busy} days={days} />
      </div>
    </>
  );
}

const SPEED_BIN_WORDS = { mph: "5 mph", kmh: "5 km/h" } as const;

function ByLine({ stats, busy }: { stats: SpeedStats; busy: boolean }) {
  const { units } = stats;
  const lines = stats.by_line.filter((line) => line.counted > 0 || line.figures !== null);
  if (lines.length === 0) return null;
  return (
    <Card aria-busy={busy} data-testid="data-speeds-by-line">
      <CardHeader>
        <CardTitle as="h3">By counting line</CardTitle>
      </CardHeader>
      <CardBody className={cn("transition-opacity", busy && "opacity-60")}>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Speeds by counting line</caption>
            <thead>
              <tr className="text-left text-ink-2">
                <th scope="col" className="pb-1.5 pr-3 font-medium">Line</th>
                <th scope="col" className="pb-1.5 pr-3 text-right font-medium" title="Vehicles with a speed, out of the vehicles the line counted">With a speed</th>
                <th scope="col" className="pb-1.5 pr-3 text-right font-medium">Average</th>
                <th scope="col" className="pb-1.5 text-right font-medium">85th</th>
              </tr>
            </thead>
            <tbody>
              {lines.map((line) => (
                <tr key={line.line} className="border-t border-grid">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-ink">
                    {line.name || (line.line === "" ? "Unknown line" : "A line that was removed")}
                  </th>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink">
                    {formatCount(line.figures?.count ?? 0)} of {formatCount(line.counted)}
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink">{line.figures ? speedValue(line.figures.mean, units) : "–"}</td>
                  <td className="py-1.5 text-right tabular-nums text-ink">{line.figures ? speedValue(line.figures.p85, units) : "–"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}

function ByHour({ stats, busy, days }: { stats: SpeedStats; busy: boolean; days: number }) {
  const { units } = stats;
  const hours = stats.by_hour.filter((hour) => hour.count > 0);
  if (hours.length === 0) return null;
  return (
    <Card aria-busy={busy} data-testid="data-speeds-by-hour">
      <CardHeader>
        <div>
          <CardTitle as="h3">By the hour of the day</CardTitle>
          <p className="mt-0.5 text-sm text-ink-2">Over {plural(days, "day")}; hours with no speed are left out.</p>
        </div>
      </CardHeader>
      <CardBody className={cn("transition-opacity", busy && "opacity-60")}>
        <div className="max-h-80 overflow-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Speeds by the hour of the day</caption>
            <thead>
              <tr className="text-left text-ink-2">
                <th scope="col" className="pb-1.5 pr-3 font-medium">Hour</th>
                <th scope="col" className="pb-1.5 pr-3 text-right font-medium">Vehicles</th>
                <th scope="col" className="pb-1.5 pr-3 text-right font-medium">Average</th>
                <th scope="col" className="pb-1.5 text-right font-medium">85th</th>
              </tr>
            </thead>
            <tbody>
              {hours.map((hour) => (
                <tr key={hour.hour} className="border-t border-grid">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-ink">{hourRange(hour.hour)}</th>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink">{formatCount(hour.count)}</td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink">{hour.mean === null ? "–" : speedValue(hour.mean, units)}</td>
                  <td className="py-1.5 text-right tabular-nums text-ink">{hour.p85 === null ? "–" : speedValue(hour.p85, units)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardBody>
    </Card>
  );
}
