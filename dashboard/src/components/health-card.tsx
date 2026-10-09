"use client";

import { useId, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import { StatTile } from "@/components/ui/stat";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  DEFAULT_HEALTH_PERIOD,
  HEALTH_PERIODS,
  axisTicks,
  describeEpisode,
  flagNames,
  frameRateDomain,
  hasRecord,
  healthRows,
  periodHours,
  summaryTiles,
  temperatureDomain,
  valueTicks,
  type HealthPeriod,
  type HealthRow,
} from "@/lib/health";
import { usePersistentState, useRequest } from "@/lib/hooks";
import { describeThrottle } from "@/lib/severity";
import type { SystemHistory } from "@/lib/types";

const REFRESH_MS = 60_000;
const TICK = { fill: "var(--ink-2)", fontSize: 12 } as const;

type SeriesKey = "temp" | "fps" | "fpsMin";

interface TrendLine {
  key: SeriesKey;
  label: string;
  color: string;
  dashed?: boolean;
}

/** How the machine has been: a record kept by the vision service, a reading a minute for 30 days. */
export function HealthCard() {
  const [stored, setStored] = usePersistentState<{ period: HealthPeriod }>("trafficpi.health", { period: DEFAULT_HEALTH_PERIOD });
  const period = HEALTH_PERIODS.some((option) => option.value === stored.period) ? stored.period : DEFAULT_HEALTH_PERIOD;
  const [view, setView] = useState<"chart" | "table">("chart");
  const hours = periodHours(period);
  const key = `system-history:${hours}`;
  const history = useRequest(key, (signal) => api.systemHistory(hours, signal), { refreshMs: REFRESH_MS });
  const data = history.data;
  const busy = data !== null && history.answeredKey !== key; // the numbers on show are being replaced
  const rows = useMemo(() => (data ? healthRows(data) : []), [data]);
  const recorded = data !== null && hasRecord(data);

  return (
    <Card aria-busy={busy} data-testid="system-history">
      <CardHeader>
        <div>
          <CardTitle>Health over time</CardTitle>
          <p className="mt-0.5 text-sm text-ink-2">A reading of the machine every minute, kept for 30 days: temperature, frame rate and the Pi&rsquo;s own warnings.</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented
            label="Period"
            value={period}
            options={HEALTH_PERIODS.map(({ value, label }) => ({ value, label }))}
            onChange={(value) => setStored({ ...stored, period: value })}
          />
          {recorded && (
            <Segmented
              label="Show as"
              value={view}
              options={[
                { value: "chart", label: "Chart" },
                { value: "table", label: "Table" },
              ]}
              onChange={setView}
            />
          )}
        </div>
      </CardHeader>
      <CardBody className={cn("grid gap-5 transition-opacity", busy && "opacity-60")}>
        {data === null ? (
          <p className="text-sm text-ink-2">{history.error ? "The history could not be loaded. Trying again." : "Loading…"}</p>
        ) : !recorded ? (
          <p className="text-sm text-ink-2" data-testid="system-history-empty">
            Nothing is recorded in this period yet. The vision service stores a reading of the machine every minute while it runs.
          </p>
        ) : (
          <>
            <dl className="grid grid-cols-2 gap-3 xl:grid-cols-4" aria-label="This period in numbers">
              {summaryTiles(data.summary, data).map((tile) => (
                <StatTile key={tile.label} label={tile.label} value={tile.value} note={tile.note} wrap>
                  {tile.flag && <StatusBadge level={tile.flag.level}>{tile.flag.text}</StatusBadge>}
                </StatTile>
              ))}
            </dl>
            {view === "chart" ? <Charts rows={rows} timeZone={data.timezone} /> : <HistoryTable rows={rows} />}
            <Episodes data={data} />
          </>
        )}
      </CardBody>
    </Card>
  );
}

function Charts({ rows, timeZone }: { rows: HealthRow[]; timeZone: string }) {
  const ticks = axisTicks(rows, timeZone);
  return (
    <div className="grid gap-5">
      <TrendChart
        title="Temperature"
        unit="°C"
        rows={rows}
        ticks={ticks}
        domain={temperatureDomain(rows)}
        lines={[{ key: "temp", label: "Hottest minute", color: "var(--series-2)" }]}
        limits={[
          { y: 80, label: "slows down", color: "var(--warning)" },
          { y: 85, label: "throttles hard", color: "var(--critical)" },
        ]}
      />
      <TrendChart
        title="Frame rate"
        unit="fps"
        rows={rows}
        ticks={ticks}
        domain={frameRateDomain(rows)}
        lines={[
          { key: "fps", label: "Average", color: "var(--series-1)" },
          { key: "fpsMin", label: "Slowest minute", color: "var(--series-1)", dashed: true },
        ]}
      />
    </div>
  );
}

function TrendChart({
  title,
  unit,
  rows,
  lines,
  domain,
  ticks,
  limits = [],
}: {
  title: string;
  unit: string;
  rows: HealthRow[];
  lines: readonly TrendLine[];
  domain: [number, number];
  /** The labels to show along the bottom: round times. */
  ticks: string[];
  limits?: readonly { y: number; label: string; color: string }[];
}) {
  const describedBy = useId();
  const values = rows.flatMap((row) => lines.map((line) => row[line.key])).filter((value): value is number => value !== null);
  const summary =
    values.length === 0
      ? `${title}: nothing was recorded.`
      : `${title}, from ${Math.min(...values).toFixed(1)} to ${Math.max(...values).toFixed(1)} ${unit}. A gap in the line is a time when nothing was recorded or the camera did not work. Switch to the table for every value.`;
  return (
    <figure>
      <figcaption className="mb-1 flex flex-wrap items-baseline gap-x-4 gap-y-0.5 text-sm">
        <span className="font-medium text-ink">
          {title} <span className="font-normal text-ink-2">({unit})</span>
        </span>
        {lines.length > 1 && (
          <ul className="flex flex-wrap gap-x-4 text-ink-2" aria-label="Key">
            {lines.map((line) => (
              <li key={line.key} className="inline-flex items-center gap-1.5">
                <svg width="18" height="6" aria-hidden="true">
                  <line x1="0" y1="3" x2="18" y2="3" stroke={line.color} strokeWidth="2" strokeDasharray={line.dashed ? "5 3" : undefined} />
                </svg>
                {line.label}
              </li>
            ))}
          </ul>
        )}
      </figcaption>
      <p className="sr-only" id={describedBy}>
        {summary}
      </p>
      <div className="h-[180px]" aria-describedby={describedBy}>
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 8, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--grid)" strokeWidth={1} />
            <XAxis dataKey="label" ticks={ticks} interval={0} tickLine={false} tick={TICK} stroke="var(--baseline)" tickMargin={6} />
            <YAxis width={36} domain={domain} ticks={valueTicks(domain)} interval={0} allowDecimals={false} tickLine={false} axisLine={false} tick={TICK} />
            <Tooltip isAnimationActive={false} cursor={{ stroke: "var(--ink-3)", strokeDasharray: "3 3" }} content={(props) => <HistoryTooltip {...props} />} />
            {limits.map((limit) => (
              <ReferenceLine
                key={limit.y}
                y={limit.y}
                stroke={limit.color}
                strokeDasharray="4 4"
                label={{ value: `${limit.y} ${unit}, ${limit.label}`, position: "insideTopLeft", fill: "var(--ink-2)", fontSize: 11 }}
              />
            ))}
            {lines.map((line) => (
              <Line
                key={line.key}
                type="linear"
                dataKey={line.key}
                stroke={line.color}
                strokeWidth={2}
                strokeDasharray={line.dashed ? "5 4" : undefined}
                dot={false}
                activeDot={{ r: 4 }}
                connectNulls={false}
                isAnimationActive={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      </div>
    </figure>
  );
}

const number = (value: number | null, digits: number, unit: string) => (value === null ? "–" : `${value.toFixed(digits)}${unit}`);

function Item({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt className="text-ink-2">{label}</dt>
      <dd className="text-right font-medium tabular-nums text-ink">{value}</dd>
    </>
  );
}

function HistoryTooltip({ active, payload }: TooltipContentProps) {
  if (!active || !payload || payload.length === 0) return null;
  const row = payload[0]?.payload as HealthRow | undefined;
  if (!row) return null;
  const flags = flagNames(row.flags).map(describeThrottle);
  return (
    <div className="min-w-52 rounded-lg border border-edge bg-surface px-3 py-2.5 text-sm shadow-lg">
      <p className="text-ink-2">{row.title}</p>
      {row.minutes === 0 ? (
        <p className="mt-0.5 text-base font-semibold text-ink">Nothing recorded</p>
      ) : (
        <dl className="mt-1 grid grid-cols-[auto_auto] gap-x-4 gap-y-0.5">
          <Item label="Hottest" value={number(row.temp, 1, " °C")} />
          <Item label="Processor load" value={number(row.cpu, 0, "%")} />
          <Item label="Frame rate" value={row.fps === null ? "camera away" : number(row.fps, 1, " fps")} />
          {row.fpsMin !== null && <Item label="Slowest minute" value={number(row.fpsMin, 1, " fps")} />}
          <Item label="Camera working" value={number(row.up, 0, "%")} />
          {flags.length > 0 && <Item label="The Pi says" value={flags.join("; ")} />}
        </dl>
      )}
    </div>
  );
}

/** The same numbers as the charts, for anyone who would rather read them: newest first, only stretches with a record. */
function HistoryTable({ rows }: { rows: HealthRow[] }) {
  const recorded = rows.filter((row) => row.minutes > 0).reverse();
  return (
    <div className="max-h-[360px] overflow-auto rounded-lg border border-edge">
      <table className="w-full text-sm">
        <caption className="sr-only">The machine&rsquo;s temperature, load and frame rate by stretch of time, newest first</caption>
        <thead className="sticky top-0 bg-sunken text-left text-ink-2">
          <tr>
            <th scope="col" className="px-3 py-2 font-medium">Time</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Hottest</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Load</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Frame rate</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Slowest</th>
            <th scope="col" className="px-3 py-2 text-right font-medium">Camera</th>
            <th scope="col" className="px-3 py-2 font-medium">The Pi says</th>
          </tr>
        </thead>
        <tbody>
          {recorded.map((row) => (
            <tr key={row.id} className="border-t border-grid">
              <th scope="row" className="whitespace-nowrap px-3 py-1.5 text-left font-normal tabular-nums text-ink">{row.title}</th>
              <td className="px-3 py-1.5 text-right tabular-nums text-ink">{number(row.temp, 1, " °C")}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-ink">{number(row.cpu, 0, "%")}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-ink">{number(row.fps, 1, " fps")}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-ink">{number(row.fpsMin, 1, " fps")}</td>
              <td className="px-3 py-1.5 text-right tabular-nums text-ink">{number(row.up, 0, "%")}</td>
              <td className="px-3 py-1.5 text-ink-2">{flagNames(row.flags).map(describeThrottle).join("; ") || "–"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** What went on for a while and is worth a sentence: the point of keeping the record. */
function Episodes({ data }: { data: SystemHistory }) {
  const headingId = useId();
  const episodes = data.episodes.map((episode) => describeEpisode(episode, data.timezone));
  return (
    <section aria-labelledby={headingId} data-testid="system-history-episodes">
      <h3 id={headingId} className="mb-2 text-sm font-medium text-ink">
        What happened
      </h3>
      {episodes.length === 0 ? (
        <p className="text-sm text-ink-2">Nothing to report: the Pi did not slow itself down, the power was steady, the camera stayed on and the record has no holes.</p>
      ) : (
        <ul className="grid gap-2.5">
          {episodes.map((episode) => (
            <li key={episode.key} className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
              <StatusBadge level={episode.level}>{episode.title}</StatusBadge>
              <span className="text-sm tabular-nums text-ink">{episode.when}</span>
              <span className="text-sm text-ink-2">{episode.detail}</span>
            </li>
          ))}
        </ul>
      )}
      {data.episodes.length >= 50 && <p className="mt-2 text-sm text-ink-2">Only the newest 50 are listed.</p>}
    </section>
  );
}
