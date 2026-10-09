import { formatCount, formatDateTime } from "./format";
import { temperatureSeverity } from "./severity";
import type { HealthEpisode, HealthSummary, SystemHistory } from "./types";

/** The stretches of the machine's past that the History card can show (the server keeps 30 days). */
export type HealthPeriod = "hour" | "6hours" | "day" | "week" | "month";

export const HEALTH_PERIODS: readonly { value: HealthPeriod; label: string; hours: number }[] = [
  { value: "hour", label: "1 hour", hours: 1 },
  { value: "6hours", label: "6 hours", hours: 6 },
  { value: "day", label: "24 hours", hours: 24 },
  { value: "week", label: "7 days", hours: 168 },
  { value: "month", label: "30 days", hours: 720 },
];

export const DEFAULT_HEALTH_PERIOD: HealthPeriod = "day";

export function periodHours(period: HealthPeriod): number {
  return HEALTH_PERIODS.find((one) => one.value === period)?.hours ?? 24;
}

/** The Pi's own flags (bits 0 to 3 of `vcgencmd get_throttled`) in the words `vision/metrics.py` uses, so `describeThrottle` knows them. */
const FLAG_BITS: readonly [number, string][] = [
  [0b0001, "under-voltage"],
  [0b0010, "ARM frequency capped"],
  [0b0100, "throttled"],
  [0b1000, "soft temperature limit"],
];

export function flagNames(bits: number): string[] {
  return FLAG_BITS.filter(([bit]) => (bits & bit) !== 0).map(([, name]) => name);
}

/** One stretch of the chart as Recharts and the table get it. A stretch with `minutes` 0 had nothing recorded in it: the numbers are null. */
export interface HealthRow {
  id: string;
  /** Seconds since the epoch, where the stretch begins. */
  t: number;
  /** The short label under the axis. */
  label: string;
  /** The full time, for the tooltip and the table. */
  title: string;
  minutes: number;
  temp: number | null;
  cpu: number | null;
  fps: number | null;
  fpsMin: number | null;
  /** The share of readings in which the camera worked, in percent. */
  up: number | null;
  flags: number;
}

const DAY_S = 86_400;

/** "14:05" for a span of a day or less, "10-09 14:05" for longer, where the time alone would repeat. */
function axisLabel(startMs: number, spanSeconds: number, timeZone: string): string {
  const text = formatDateTime(startMs, timeZone);
  return spanSeconds <= DAY_S ? text.slice(11) : text.slice(5);
}

/** "2026-10-09 14:00", or "2026-10-09 14:00–14:10" for a stretch of more than a minute (with the date again if it ends on another day). */
function rangeTitle(startSeconds: number, bucketSeconds: number, timeZone: string): string {
  const start = formatDateTime(startSeconds * 1000, timeZone);
  if (bucketSeconds <= 60) return start;
  const end = formatDateTime((startSeconds + bucketSeconds) * 1000, timeZone);
  return `${start}–${end.slice(0, 10) === start.slice(0, 10) ? end.slice(11) : end}`;
}

export function healthRows(history: SystemHistory): HealthRow[] {
  const span = history.to_s - history.from_s;
  return history.points.map((point) => ({
    id: String(point.t),
    t: point.t,
    label: axisLabel(point.t * 1000, span, history.timezone),
    title: rangeTitle(point.t, history.bucket_s, history.timezone),
    minutes: point.minutes,
    temp: point.temp_c ?? null,
    cpu: point.cpu_percent ?? null,
    fps: point.fps ?? null,
    fpsMin: point.fps_min ?? null,
    up: point.camera_up_percent ?? null,
    flags: point.throttled,
  }));
}

/** The temperature axis: from 30 degrees to above both the hottest reading and the 85 degrees at which the Pi throttles hard, in tens. */
export function temperatureDomain(rows: readonly HealthRow[]): [number, number] {
  const hottest = Math.max(0, ...rows.map((row) => row.temp ?? 0));
  return [30, Math.max(90, Math.ceil((hottest + 2) / 10) * 10)];
}

/** The frame-rate axis: from nothing to above the best reading, in tens, at least 40. */
export function frameRateDomain(rows: readonly HealthRow[]): [number, number] {
  const best = Math.max(0, ...rows.map((row) => Math.max(row.fps ?? 0, row.fpsMin ?? 0)));
  return [0, Math.max(40, Math.ceil((best + 1) / 10) * 10)];
}

/** The marks along a value axis: every ten, from the bottom to the top. */
export function valueTicks([low, high]: readonly [number, number]): number[] {
  const ticks: number[] = [];
  for (let value = low; value <= high; value += 10) ticks.push(value);
  return ticks;
}

/**
 * The labels to show along the time axis, at round times instead of wherever a fixed number of stretches happens to land: every ten
 * minutes for an hour, every hour for six, every four hours for a day, and the start of each day (of every few days, for a month)
 * for longer. "Round" is by the clock of the camera's zone, so it is the same in a zone that is not a whole number of hours from UTC.
 */
export function axisTicks(rows: readonly HealthRow[], timeZone: string): string[] {
  const first = rows[0];
  const last = rows.at(-1);
  if (first === undefined || last === undefined || rows.length < 2) return [];
  const spanHours = (last.t - first.t) / 3600;
  const local = rows.map((row) => {
    const text = formatDateTime(row.t * 1000, timeZone); // "2026-10-09 14:05"
    return { label: row.label, date: text.slice(0, 10), minuteOfDay: Number(text.slice(11, 13)) * 60 + Number(text.slice(14, 16)) };
  });
  if (spanHours > 36) {
    const starts = local.filter((row, i) => (i === 0 ? row.minuteOfDay === 0 : row.date !== local[i - 1]?.date));
    const every = Math.max(1, Math.ceil(starts.length / 8));
    return starts.filter((_, i) => i % every === 0).map((row) => row.label);
  }
  const step = spanHours <= 2 ? 10 : spanHours <= 12 ? 60 : 240; // minutes
  return local.filter((row, i) => (i === 0 ? row.minuteOfDay % step === 0 : Math.floor(row.minuteOfDay / step) !== Math.floor((local[i - 1]?.minuteOfDay ?? 0) / step))).map((row) => row.label);
}

/** Whether anything was recorded in the period at all. */
export function hasRecord(history: SystemHistory): boolean {
  return history.summary.minutes > 0;
}

/** Seconds as people say how long something lasted: "7 min", "1 h 05 min", "2 d 3 h". */
export function minutesText(totalSeconds: number): string {
  const minutes = Math.max(1, Math.round(totalSeconds / 60));
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} h ${String(minutes % 60).padStart(2, "0")} min`;
  return `${Math.floor(hours / 24)} d ${hours % 24} h`;
}

export type EpisodeLevel = "warning" | "critical" | "neutral";

export interface EpisodeText {
  key: string;
  level: EpisodeLevel;
  title: string;
  /** When, and for how long. */
  when: string;
  detail: string;
}

/** An episode in words: what happened, when, and what it means. */
export function describeEpisode(episode: HealthEpisode, timeZone: string): EpisodeText {
  const start = formatDateTime(episode.from_s * 1000, timeZone);
  const end = formatDateTime(episode.to_s * 1000, timeZone);
  const duration = minutesText(episode.to_s - episode.from_s);
  const when = episode.ongoing
    ? `${start} until now (${duration} so far)`
    : `${start} to ${end.slice(0, 10) === start.slice(0, 10) ? end.slice(11) : end} (${duration})`;
  const key = `${episode.kind}:${episode.from_s}`;
  switch (episode.kind) {
    case "slowed":
      return {
        key,
        level: "warning",
        title: "Slowed itself down",
        when,
        detail: `${episode.peak_temp_c == null ? "" : `Up to ${episode.peak_temp_c.toFixed(1)} °C. `}The processor ran slower, to cool down or because the supply was weak.`,
      };
    case "under-voltage":
      return { key, level: "warning", title: "Power supply too weak", when, detail: "The supply could not deliver enough. The board wants a 27 W one (5 V, 5 A)." };
    case "camera-offline":
      return { key, level: "critical", title: "Camera not working", when, detail: "No picture reached the program, so nothing was counted." };
    case "no-data":
      return {
        key,
        level: "neutral",
        title: "Nothing recorded",
        when,
        detail: "The vision service was not running: the Pi was off or restarting, or the service had stopped.",
      };
  }
}

export interface Tile {
  label: string;
  value: string;
  note: string;
  /** A word of warning to put by the number, when it calls for one (always with words: colour alone is not enough). */
  flag?: { level: "warning" | "critical"; text: string };
}

const percentText = (value: number) => `${value.toFixed(1)}%`;

/** The few numbers over the charts. */
export function summaryTiles(summary: HealthSummary, history: Pick<SystemHistory, "from_s" | "to_s">): Tile[] {
  const periodMinutes = Math.max(1, Math.round((history.to_s - history.from_s) / 60));
  const recorded = Math.min(100, (100 * summary.minutes) / periodMinutes);
  const heat = summary.temp_c_max == null ? "ok" : temperatureSeverity(summary.temp_c_max);
  return [
    {
      label: "Hottest",
      value: summary.temp_c_max == null ? "–" : `${summary.temp_c_max.toFixed(1)} °C`,
      note: "the processor's hottest minute",
      flag:
        heat === "critical"
          ? { level: "critical", text: "past 85 °C, where the Pi throttles hard" }
          : heat === "warning"
            ? { level: "warning", text: "past 80 °C, where the Pi slows itself down" }
            : undefined,
    },
    {
      label: "Slowed down",
      value: summary.slowed_minutes === 0 ? "never" : minutesText(summary.slowed_minutes * 60),
      note: summary.under_voltage_minutes === 0 ? "the Pi holding itself back" : `and ${minutesText(summary.under_voltage_minutes * 60)} of weak power supply`,
      flag: summary.slowed_minutes > 0 || summary.under_voltage_minutes > 0 ? { level: "warning", text: "listed below" } : undefined,
    },
    {
      label: "Camera working",
      value: summary.camera_up_percent == null ? "–" : percentText(summary.camera_up_percent),
      note: "of the time the service was recording",
    },
    {
      label: "Recorded",
      value: `${formatCount(summary.minutes)} min`,
      note: `a reading each minute, ${Math.round(recorded)}% of the period`,
    },
  ];
}
