"use client";

import { useId, useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis, type TooltipContentProps } from "recharts";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { StatTile } from "@/components/ui/stat";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  DEFAULT_HEALTH_PERIOD,
  EPISODE_MEANING,
  HEALTH_PERIODS,
  axisTicks,
  describeEpisode,
  episodeSummary,
  flagNames,
  frameRateDomain,
  hasRecord,
  healthRows,
  periodHours,
  summaryTiles,
  temperatureDomain,
  trimLeadingGap,
  valueTicks,
  type EpisodeRow,
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
  const allRows = useMemo(() => (data ? healthRows(data) : []), [data]);
  // A record that began this afternoon has no chart that is empty until then: it starts where the record does.
  const rows = useMemo(() => trimLeadingGap(allRows), [allRows]);
  const trimmed = allRows.length - rows.length;
  const recorded = data !== null && hasRecord(data);

  return (
    <Card aria-busy={busy} data-testid="system-history">
      <CardHeader>
        <div>
          <CardTitle>Health over time</CardTitle>
          <CardSubtitle>A reading of the machine every minute, kept for 30 days.</CardSubtitle>
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
                  {tile.flag &&
                    (tile.flag.href ? (
                      <a href={tile.flag.href} className="inline-block rounded-lg underline-offset-2 hover:underline">
                        <StatusBadge level={tile.flag.level}>{tile.flag.text}</StatusBadge>
                      </a>
                    ) : (
                      <StatusBadge level={tile.flag.level}>{tile.flag.text}</StatusBadge>
                    ))}
                </StatTile>
              ))}
            </dl>
            {trimmed > 0 && rows[0] && <p className="text-sm text-ink-3">The record begins {rows[0].title}; the time before it is left off the charts.</p>}
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
          { y: 80, label: "throttles", color: "var(--warning)" },
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
        {(lines.length > 1 || limits.length > 0) && (
          <ul className="flex flex-wrap gap-x-4 text-ink-2" aria-label="Key">
            {lines.length > 1 &&
              lines.map((line) => (
                <li key={line.key} className="inline-flex items-center gap-1.5">
                  <svg width="18" height="6" aria-hidden="true">
                    <line x1="0" y1="3" x2="18" y2="3" stroke={line.color} strokeWidth="2" strokeDasharray={line.dashed ? "5 3" : undefined} />
                  </svg>
                  {line.label}
                </li>
              ))}
            {/* The limits are named here, at a size that reads, instead of on the lines, where two labels five degrees apart ran into each other. */}
            {limits.map((limit) => (
              <li key={limit.y} className="inline-flex items-center gap-1.5">
                <svg width="18" height="6" aria-hidden="true">
                  <line x1="0" y1="3" x2="18" y2="3" stroke={limit.color} strokeWidth="2" strokeDasharray="4 3" />
                </svg>
                {limit.y} {unit} {limit.label}
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
          <Item label="Camera uptime" value={number(row.up, 0, "%")} />
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
            <th scope="col" className="px-3 py-2 text-right font-medium">Camera uptime</th>
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

/** The most episodes listed before "Show all": a bad day has dozens, and the summary line above them already says how many. */
const EPISODES_SHOWN = 8;

/**
 * What went on for a while and is worth a sentence, the point of keeping the record: one line that
 * sums it up, then a table (the day once, then when, how long, how hot) and what each kind means said
 * once under it, not in every row.
 */
function Episodes({ data }: { data: SystemHistory }) {
  const headingId = useId();
  const [all, setAll] = useState(false);
  const rows = data.episodes.map((episode) => describeEpisode(episode, data.timezone));
  const shown = all ? rows : rows.slice(0, EPISODES_SHOWN);
  const days = [...new Set(shown.map((row) => row.date))];
  const kinds = [...new Set(rows.map((row) => row.kind))];
  return (
    <section aria-labelledby={headingId} id="health-episodes" data-testid="system-history-episodes" className="scroll-mt-20">
      <h3 id={headingId} className="mb-1 text-sm font-semibold text-ink">
        What happened
      </h3>
      {rows.length === 0 ? (
        <p className="text-sm text-ink-2">Nothing to report: the Pi was not throttled, the power was steady, the camera stayed on and the record has no holes.</p>
      ) : (
        <>
          <p className="mb-2 text-sm text-ink-2" data-testid="system-history-summary">
            {episodeSummary(data.episodes)}
          </p>
          <div className="max-w-3xl overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">What happened to the machine, newest first</caption>
              <thead>
                <tr className="text-left text-ink-2">
                  <th scope="col" className="pb-1.5 pr-3 font-medium">What</th>
                  <th scope="col" className="pb-1.5 pr-3 font-medium">Time</th>
                  <th scope="col" className="pb-1.5 pr-3 text-right font-medium">Duration</th>
                  <th scope="col" className="pb-1.5 text-right font-medium">Peak</th>
                </tr>
              </thead>
              <tbody>
                {days.map((day) => (
                  <EpisodeDay key={day} day={day} rows={shown.filter((row) => row.date === day)} />
                ))}
              </tbody>
            </table>
          </div>
          {rows.length > EPISODES_SHOWN && (
            <Button size="sm" className="mt-2" onClick={() => setAll((was) => !was)}>
              {all ? `Show the newest ${EPISODES_SHOWN}` : `Show all ${rows.length}`}
            </Button>
          )}
          <ul className="mt-3 grid gap-1 text-sm text-ink-3">
            {kinds.map((kind) => (
              <li key={kind}>
                <span className="font-medium text-ink-2">{rows.find((row) => row.kind === kind)?.title}:</span> {EPISODE_MEANING[kind]}
              </li>
            ))}
          </ul>
        </>
      )}
      {data.episodes.length >= 50 && <p className="mt-2 text-sm text-ink-2">Only the newest 50 are listed.</p>}
    </section>
  );
}

/** One day's episodes: the day as a line of its own, then a row for each, so that the date is not said again and again. */
function EpisodeDay({ day, rows }: { day: string; rows: EpisodeRow[] }) {
  return (
    <>
      <tr>
        <th scope="colgroup" colSpan={4} className="pb-1 pt-3 text-left text-sm font-medium text-ink-2">
          {day}
        </th>
      </tr>
      {rows.map((row) => (
        <tr key={row.key} className="border-t border-grid">
          <td className="py-1.5 pr-3 align-top">
            <StatusBadge level={row.level}>{row.title}</StatusBadge>
          </td>
          <td className="py-1.5 pr-3 align-top tabular-nums text-ink">
            {row.start}–{row.ongoing ? "now" : row.end}
          </td>
          <td className="py-1.5 pr-3 text-right align-top tabular-nums text-ink">{row.duration}</td>
          <td className="py-1.5 text-right align-top tabular-nums text-ink">{row.peak ?? "–"}</td>
        </tr>
      ))}
    </>
  );
}
