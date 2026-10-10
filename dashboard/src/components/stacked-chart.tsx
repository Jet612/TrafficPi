"use client";

import { useId, useMemo, useState } from "react";
import {
  Bar,
  BarChart,
  BarStack,
  CartesianGrid,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts";
import type { LucideIcon } from "lucide-react";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { ClassIcon } from "@/components/ui/class-icon";
import { Segmented } from "@/components/ui/segmented";
import { cn } from "@/lib/cn";
import { asShares, peak, trimEmptyLead, type ChartRow, type ChartSeries } from "@/lib/data";
import { formatCount } from "@/lib/format";
import { axisTicks, axisWidth, niceMax } from "@/lib/hourly";

const TICK = { fill: "var(--ink-2)", fontSize: 12 } as const;

/** A column of the chart as Recharts gets it: the row's words, then each series' count (`c:`) and share (`s:`). */
type DataRow = { id: string; label: string; title: string; total: number } & { [field: `${"c" | "s"}:${string}`]: number };

const countField = (key: string) => `c:${key}` as const;
const shareField = (key: string) => `s:${key}` as const;

const percent = (n: number) => `${Math.round(n)}%`;
const plural = (n: number, unit: string) => `${formatCount(n)} ${unit}${n === 1 ? "" : "s"}`;

export interface StackedChartProps {
  title: string;
  subtitle?: React.ReactNode;
  rows: readonly ChartRow[];
  /** From the baseline up. */
  series: readonly ChartSeries[];
  /** What a row's total counts, singular: "vehicle". */
  unit: string;
  /** A key beside a series' name in the legend, for series whose colour must not stand alone. */
  icons?: Readonly<Record<string, LucideIcon>>;
  /** Offer the shares of each column as well as the counts. */
  shareToggle?: boolean;
  /** The series are kinds of vehicle: the key shows each kind's icon. */
  kinds?: boolean;
  /** Begin at the first column that has anything in it (a day chart of a record that began this week). */
  startAtData?: boolean;
  /** The widest a bar may be, in pixels: a column of an hourly chart is slim, one of a daily chart has room. */
  maxBar?: number;
  /** How many axis labels to skip between two that are shown. */
  interval?: number;
  emptyText: string;
  /** The heading of the table's first column. */
  firstColumn: string;
  /** What the chart says, for a screen reader. */
  summary: string;
  /** More columns for the table, after the counts. */
  extraColumns?: readonly { header: string; cell: (row: ChartRow) => string }[];
  /** The numbers on show are being replaced: dim them instead of blanking them. */
  busy?: boolean;
  testId?: string;
}

/**
 * Columns stacked by series, as a chart or as the same numbers in a table. One of these serves the
 * outcomes at the stop signs (by hour, by day) and the vehicles counted (by hour, by day).
 */
export function StackedChartCard({
  title,
  subtitle,
  rows: allRows,
  series,
  unit,
  icons,
  shareToggle = false,
  kinds = false,
  startAtData = false,
  maxBar = 48,
  interval = 0,
  emptyText,
  firstColumn,
  summary,
  extraColumns = [],
  busy = false,
  testId,
}: StackedChartProps) {
  // One control for how to look at it: the counts or the shares as a chart, or the table. (Two controls side by side read as one.)
  const [shown, setShown] = useState<"chart" | "count" | "share" | "table">(shareToggle ? "count" : "chart");
  const view = shown === "table" ? "table" : "chart";
  const shares = shown === "share";
  const columns = useMemo(() => (startAtData ? trimEmptyLead(allRows) : allRows), [allRows, startAtData]);
  const began = startAtData && columns.length < allRows.length ? columns[0]?.title : undefined;

  const data = useMemo<DataRow[]>(() => {
    const asPercent = asShares(columns);
    return columns.map((row, index) => {
      const fields: Record<string, number> = {};
      for (const one of series) {
        fields[countField(one.key)] = row.values[one.key] ?? 0;
        fields[shareField(one.key)] = asPercent[index]?.values[one.key] ?? 0;
      }
      return { id: row.id, label: row.label, title: row.title, total: row.total, ...fields };
    });
  }, [columns, series]);

  const highest = useMemo(() => peak(columns, (row) => row.total), [columns]);
  const max = shares ? 100 : niceMax(highest?.value ?? 0);
  const ticks = shares ? [0, 25, 50, 75, 100] : axisTicks(max);
  const empty = highest === null;
  const describedBy = useId();

  return (
    <Card data-testid={testId} aria-busy={busy}>
      <CardHeader>
        <div>
          <CardTitle as="h3">{title}</CardTitle>
          {(subtitle || began) && (
            <CardSubtitle>
              {subtitle}
              {subtitle && began ? " " : ""}
              {began && `Counting began ${began}.`}
            </CardSubtitle>
          )}
        </div>
        <Segmented
          label="Show as"
          value={shown}
          options={
            shareToggle
              ? [
                  { value: "count", label: "Count" },
                  { value: "share", label: "Share" },
                  { value: "table", label: "Table" },
                ]
              : [
                  { value: "chart", label: "Chart" },
                  { value: "table", label: "Table" },
                ]
          }
          onChange={setShown}
        />
      </CardHeader>
      <CardBody className={cn("transition-opacity", busy && "opacity-60")}>
        {view === "table" ? (
          <ChartTable rows={allRows} series={series} firstColumn={firstColumn} caption={title} extraColumns={extraColumns} />
        ) : (
          <>
            {series.length > 1 && <Legend series={series} icons={icons} kinds={kinds} />}
            <p className="sr-only" id={describedBy}>
              {summary} Switch to the table for every value.
            </p>
            <div className="relative h-[260px]" aria-describedby={describedBy}>
              {empty && <p className="pointer-events-none absolute inset-0 z-10 grid place-items-center pb-8 text-sm text-ink-2">{emptyText}</p>}
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={data} margin={{ top: 22, right: 4, bottom: 0, left: 0 }} barCategoryGap="22%">
                  <CartesianGrid vertical={false} stroke="var(--grid)" strokeWidth={1} />
                  <XAxis dataKey="label" interval={interval} tickLine={false} tick={TICK} stroke="var(--baseline)" tickMargin={6} />
                  <YAxis
                    width={axisWidth(max, shares)}
                    domain={[0, max]}
                    ticks={ticks}
                    allowDecimals={false}
                    tickLine={false}
                    axisLine={false}
                    tick={TICK}
                    tickFormatter={(value: number) => (shares ? `${value}%` : formatCount(value))}
                  />
                  <Tooltip
                    isAnimationActive={false}
                    cursor={{ fill: "var(--ink)", fillOpacity: 0.06 }}
                    content={(props) => <ChartTooltip {...props} series={series} unit={unit} kinds={kinds} />}
                  />
                  <BarStack radius={[4, 4, 0, 0]}>
                    {series.map((one) => (
                      <Bar
                        key={one.key}
                        dataKey={shares ? shareField(one.key) : countField(one.key)}
                        fill={one.color}
                        stroke="var(--surface)"
                        strokeWidth={2}
                        maxBarSize={maxBar}
                        isAnimationActive={false}
                      />
                    ))}
                  </BarStack>
                  {!shares && highest && (
                    <ReferenceDot
                      x={highest.item.label}
                      y={highest.value}
                      r={0}
                      stroke="none"
                      label={{ value: formatCount(highest.value), position: "top", fill: "var(--ink)", fontSize: 12, fontWeight: 600 }}
                    />
                  )}
                </BarChart>
              </ResponsiveContainer>
            </div>
          </>
        )}
      </CardBody>
    </Card>
  );
}

function Legend({ series, icons, kinds }: { series: readonly ChartSeries[]; icons?: Readonly<Record<string, LucideIcon>>; kinds: boolean }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-sm text-ink-2" aria-label="Key">
      {series.map((one) => {
        const Icon = icons?.[one.key];
        return (
          <li key={one.key} className="inline-flex items-center gap-1.5">
            {Icon ? (
              <Icon className="size-4 shrink-0" style={{ color: one.color }} aria-hidden="true" />
            ) : kinds ? (
              <ClassIcon name={one.key} />
            ) : (
              <span className="size-2.5 rounded-[3px]" style={{ background: one.color }} aria-hidden="true" />
            )}
            {one.label}
          </li>
        );
      })}
    </ul>
  );
}

function ChartTooltip({ active, payload, series, unit, kinds }: TooltipContentProps & { series: readonly ChartSeries[]; unit: string; kinds: boolean }) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload as DataRow | undefined;
  if (!row) return null;
  const present = [...series].reverse().filter((one) => (row[countField(one.key)] ?? 0) > 0); // the top of the stack first
  return (
    <div className="min-w-48 rounded-lg border border-edge bg-surface px-3 py-2.5 text-sm shadow-lg">
      <p className="text-ink-2">{row.title}</p>
      <p className="mt-0.5 text-base font-semibold text-ink">{row.total === 0 ? `No ${unit}s` : plural(row.total, unit)}</p>
      {present.length > 0 && (
        <ul className="mt-1.5 flex flex-col gap-1">
          {present.map((one) => (
            <li key={one.key} className="flex items-center gap-2">
              {kinds ? <ClassIcon name={one.key} /> : <span className="h-[3px] w-3 shrink-0 rounded-full" style={{ background: one.color }} aria-hidden="true" />}
              <span className="text-ink-2">{one.label}</span>
              <span className="ml-auto pl-3 font-medium tabular-nums text-ink">
                {formatCount(row[countField(one.key)] ?? 0)}
                <span className="ml-1.5 font-normal text-ink-2">{percent(row[shareField(one.key)] ?? 0)}</span>
              </span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** The same numbers as the chart, for anyone who would rather read them. */
function ChartTable({
  rows,
  series,
  firstColumn,
  caption,
  extraColumns,
}: {
  rows: readonly ChartRow[];
  series: readonly ChartSeries[];
  firstColumn: string;
  caption: string;
  extraColumns: readonly { header: string; cell: (row: ChartRow) => string }[];
}) {
  return (
    <div className="max-h-[320px] overflow-auto rounded-lg border border-edge">
      <table className="w-full text-sm">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 bg-sunken text-left text-ink-2">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">{firstColumn}</th>
            {series.map((one) => (
              <th key={one.key} scope="col" className="px-3 py-2 text-right font-medium">{one.label}</th>
            ))}
            <th scope="col" className="px-3 py-2 text-right font-medium">Total</th>
            {extraColumns.map((column) => (
              <th key={column.header} scope="col" className="px-3 py-2 text-right font-medium">{column.header}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.id} className="border-t border-grid">
              <th scope="row" className="whitespace-nowrap px-3 py-1.5 text-left font-normal tabular-nums text-ink">{row.title}</th>
              {series.map((one) => (
                <td key={one.key} className="px-3 py-1.5 text-right tabular-nums text-ink">{formatCount(row.values[one.key] ?? 0)}</td>
              ))}
              <td className="px-3 py-1.5 text-right font-medium tabular-nums text-ink">{formatCount(row.total)}</td>
              {extraColumns.map((column) => (
                <td key={column.header} className="px-3 py-1.5 text-right tabular-nums text-ink">{column.cell(row)}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
