"use client";

import { useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  BarStack,
  CartesianGrid,
  ReferenceArea,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { useStats } from "@/components/stats-provider";
import { cn } from "@/lib/cn";
import { useNow } from "@/lib/hooks";
import { classColor, classLabel } from "@/lib/classes";
import { formatCount, hourRange } from "@/lib/format";
import { axisTicks, buildHourlyRows, countFor, niceMax, peakRow, stackKeys, type HourRow } from "@/lib/hourly";

/** One column of the chart: the hour's own fields, then one count per stack key, as `n:<key>`. */
type ChartRow = Omit<HourRow, "byClass"> & { [count: `n:${string}`]: number };

const field = (key: string) => `n:${key}` as const;

const TICK = { fill: "var(--ink-2)", fontSize: 12 } as const;

export function HourlyChart() {
  const { today, series, loaded } = useStats();
  const [view, setView] = useState<"chart" | "table">("chart");
  const now = useNow(30_000);

  const model = useMemo(() => {
    if (!today) return null;
    const rows = buildHourlyRows(today, series, now);
    const keys = stackKeys(rows);
    const data: ChartRow[] = rows.map((row) => {
      const { t, hour, label, current, total } = row;
      const counts = Object.fromEntries(keys.map((key) => [field(key), countFor(row, key)]));
      return { t, hour, label, current, total, ...counts };
    });
    const peak = peakRow(rows);
    return { rows, keys, data, peak, max: niceMax(peak?.total ?? 0), current: rows.find((row) => row.current) };
  }, [today, series, now]);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Counted per hour</CardTitle>
          {model?.peak && (
            <p className="mt-0.5 text-sm text-ink-2">
              Busiest hour {hourRange(model.peak.hour)} with {formatCount(model.peak.total)}
            </p>
          )}
        </div>
        <div role="group" aria-label="Show as" className="flex rounded-lg border border-edge p-0.5 text-sm">
          {(["chart", "table"] as const).map((option) => (
            <button
              key={option}
              type="button"
              aria-pressed={view === option}
              onClick={() => setView(option)}
              className={cn("rounded-md px-3 py-1 font-medium capitalize", view === option ? "bg-sunken text-ink" : "text-ink-2 hover:text-ink")}
            >
              {option}
            </button>
          ))}
        </div>
      </CardHeader>
      <CardBody>
        {!model ? (
          <div className="grid h-[260px] place-items-center text-sm text-ink-2">{loaded ? "No data yet." : "Loading…"}</div>
        ) : view === "table" ? (
          <HourTable rows={model.rows} keys={model.keys} />
        ) : (
          <>
            {model.keys.length > 1 && <Legend keys={model.keys} />}
            <p className="sr-only" id="hourly-summary">
              Vehicles counted per hour today, stacked by kind.
              {model.peak ? ` The busiest hour is ${hourRange(model.peak.hour)} with ${model.peak.total}.` : " Nothing has been counted yet."} Switch to the table for every value.
            </p>
            <div className="relative h-[260px]" aria-describedby="hourly-summary">
              {model.peak === null && (
                <p className="pointer-events-none absolute inset-0 z-10 grid place-items-center pb-8 text-sm text-ink-2">Nothing counted yet today</p>
              )}
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={model.data} margin={{ top: 22, right: 4, bottom: 0, left: 0 }} barCategoryGap="22%">
                  <CartesianGrid vertical={false} stroke="var(--grid)" strokeWidth={1} />
                  {model.current && <ReferenceArea x1={model.current.label} x2={model.current.label} fill="var(--ink)" fillOpacity={0.06} stroke="none" />}
                  <XAxis dataKey="label" interval={2} tickLine={false} tick={TICK} stroke="var(--baseline)" tickMargin={6} />
                  <YAxis
                    width={36}
                    domain={[0, model.max]}
                    ticks={axisTicks(model.max)}
                    allowDecimals={false}
                    tickLine={false}
                    axisLine={false}
                    tick={TICK}
                    tickFormatter={(value: number) => formatCount(value)}
                  />
                  <Tooltip
                    isAnimationActive={false}
                    cursor={{ fill: "var(--ink)", fillOpacity: 0.06 }}
                    content={(props) => <HourTooltip {...props} keys={model.keys} />}
                  />
                  <BarStack radius={[4, 4, 0, 0]}>
                    {model.keys.map((key) => (
                      <Bar key={key} dataKey={field(key)} fill={classColor(key)} stroke="var(--surface)" strokeWidth={2} maxBarSize={20} isAnimationActive={false} />
                    ))}
                  </BarStack>
                  {model.peak && (
                    <ReferenceDot
                      x={model.peak.label}
                      y={model.peak.total}
                      r={0}
                      stroke="none"
                      label={{ value: formatCount(model.peak.total), position: "top", fill: "var(--ink)", fontSize: 12, fontWeight: 600 }}
                    />
                  )}
                </BarChart>
              </ResponsiveContainer>
            </div>
            {model.current && <p className="mt-2 text-sm text-ink-2">The shaded hour is still in progress.</p>}
          </>
        )}
      </CardBody>
    </Card>
  );
}

function Legend({ keys }: { keys: string[] }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-2" aria-label="Kinds of vehicle">
      {keys.map((key) => (
        <li key={key} className="inline-flex items-center gap-1.5">
          <span className="size-2.5 rounded-[3px]" style={{ background: classColor(key) }} aria-hidden="true" />
          {classLabel(key)}
        </li>
      ))}
    </ul>
  );
}

function HourTooltip({ active, payload, keys }: TooltipContentProps & { keys: string[] }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload as ChartRow | undefined;
  if (!row) return null;
  const present = [...keys].reverse().filter((key) => (row[field(key)] ?? 0) > 0); // top of the stack first
  return (
    <div className="min-w-44 rounded-lg border border-edge bg-surface px-3 py-2.5 text-sm shadow-lg">
      <p className="text-ink-2">
        {hourRange(row.hour)}
        {row.current && " · in progress"}
      </p>
      <p className="mt-0.5 text-base font-semibold text-ink">{formatCount(row.total)} counted</p>
      {present.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {present.map((key) => (
            <li key={key} className="flex items-center gap-2">
              <span className="h-[3px] w-3 shrink-0 rounded-full" style={{ background: classColor(key) }} aria-hidden="true" />
              <span className="text-ink-2">{classLabel(key)}</span>
              <span className="ml-auto pl-3 font-medium tabular-nums text-ink">{formatCount(row[field(key)] ?? 0)}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The same numbers as the chart, for anyone who would rather read them. */
function HourTable({ rows, keys }: { rows: HourRow[]; keys: string[] }) {
  return (
    <div className="max-h-[300px] overflow-auto rounded-lg border border-edge">
      <table className="w-full text-sm">
        <caption className="sr-only">Vehicles counted per hour today, by kind</caption>
        <thead className="sticky top-0 bg-sunken text-left text-ink-2">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">Hour</th>
            {keys.map((key) => (
              <th key={key} scope="col" className="px-3 py-2 text-right font-medium">{classLabel(key)}</th>
            ))}
            <th scope="col" className="px-3 py-2 text-right font-medium">Total</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.t} className="border-t border-grid">
              <th scope="row" className="px-3 py-1.5 text-left font-normal tabular-nums text-ink">
                {hourRange(row.hour)}
                {row.current && <span className="ml-2 text-ink-2">now</span>}
              </th>
              {keys.map((key) => (
                <td key={key} className="px-3 py-1.5 text-right tabular-nums text-ink">{formatCount(countFor(row, key))}</td>
              ))}
              <td className="px-3 py-1.5 text-right font-medium tabular-nums text-ink">{formatCount(row.total)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
