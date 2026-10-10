import { dayText, formatCount, formatDateTime, shortDayText, yearIn } from "./format";
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

/** "14:05" for a span of a day or less, "Oct 9 14:05" for longer, where the time alone would repeat. */
function axisLabel(startMs: number, spanSeconds: number, timeZone: string): string {
  const text = formatDateTime(startMs, timeZone);
  return spanSeconds <= DAY_S ? text.slice(11) : `${shortDayText(text.slice(0, 10))} ${text.slice(11)}`;
}

/** "Fri, Oct 9, 14:00", or "Fri, Oct 9, 14:00–14:10" for a stretch of more than a minute (with the day again if it ends on another day). */
function rangeTitle(startSeconds: number, bucketSeconds: number, timeZone: string, thisYear: number): string {
  const start = formatDateTime(startSeconds * 1000, timeZone);
  const startText = `${dayText(start.slice(0, 10), thisYear)}, ${start.slice(11)}`;
  if (bucketSeconds <= 60) return startText;
  const end = formatDateTime((startSeconds + bucketSeconds) * 1000, timeZone);
  return `${startText}–${end.slice(0, 10) === start.slice(0, 10) ? end.slice(11) : `${dayText(end.slice(0, 10), thisYear)}, ${end.slice(11)}`}`;
}

export function healthRows(history: SystemHistory): HealthRow[] {
  const span = history.to_s - history.from_s;
  const thisYear = yearIn(history.to_s * 1000, history.timezone);
  return history.points.map((point) => ({
    id: String(point.t),
    t: point.t,
    label: axisLabel(point.t * 1000, span, history.timezone),
    title: rangeTitle(point.t, history.bucket_s, history.timezone, thisYear),
    minutes: point.minutes,
    temp: point.temp_c ?? null,
    cpu: point.cpu_percent ?? null,
    fps: point.fps ?? null,
    fpsMin: point.fps_min ?? null,
    up: point.camera_up_percent ?? null,
    flags: point.throttled,
  }));
}

/**
 * The rows from the first one that has anything recorded: a record that began this afternoon does not
 * have a chart that is empty until then. The rows before it are the time the Pi was not being watched
 * yet, which `recordStart` says in words. Everything is kept when the first row has a record.
 */
export function trimLeadingGap(rows: readonly HealthRow[]): HealthRow[] {
  const first = rows.findIndex((row) => row.minutes > 0);
  return first <= 0 ? [...rows] : rows.slice(first);
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

/** What an episode is, in the words the whole page uses for it: one word for the Pi slowing itself down. */
export const EPISODE_TITLE: Record<HealthEpisode["kind"], string> = {
  slowed: "Throttled",
  "under-voltage": "Weak power",
  "camera-offline": "Camera not working",
  "no-data": "Nothing recorded",
};

/** What each kind means, said once under the list instead of in every row. */
export const EPISODE_MEANING: Record<HealthEpisode["kind"], string> = {
  slowed: "The processor ran slower, to cool down or because the supply was weak.",
  "under-voltage": "The supply could not deliver enough. The board wants a 27 W one (5 V, 5 A).",
  "camera-offline": "No picture reached the program, so nothing was counted.",
  "no-data": "The vision service was not running: the Pi was off or restarting, or the service had stopped.",
};

/** From this reading the Pi throttles hard, and a slowdown that got here is serious. */
export const HARD_LIMIT_C = 85;

export interface EpisodeRow {
  key: string;
  kind: HealthEpisode["kind"];
  level: EpisodeLevel;
  title: string;
  /** The day it began, "Fri, Oct 9": the list says it once above the episodes of that day. */
  date: string;
  /** The clock time it began, "14:00". */
  start: string;
  /** When it ended, "14:07", with the day if that is another one; null while it goes on. */
  end: string | null;
  /** "7 min", or "7 min so far" while it goes on. */
  duration: string;
  /** The hottest it got, "84.2 °C", for a slowdown that has a reading. */
  peak: string | null;
  ongoing: boolean;
}

/** An episode as one row of the table: what happened, when and for how long. */
export function describeEpisode(episode: HealthEpisode, timeZone: string): EpisodeRow {
  const start = formatDateTime(episode.from_s * 1000, timeZone);
  const end = formatDateTime(episode.to_s * 1000, timeZone);
  const thisYear = yearIn(episode.to_s * 1000, timeZone);
  const duration = minutesText(episode.to_s - episode.from_s);
  const peak = episode.kind === "slowed" && episode.peak_temp_c != null ? episode.peak_temp_c : null;
  const level: EpisodeLevel =
    episode.kind === "camera-offline" || (peak !== null && peak >= HARD_LIMIT_C) ? "critical" : episode.kind === "no-data" ? "neutral" : "warning";
  return {
    key: `${episode.kind}:${episode.from_s}`,
    kind: episode.kind,
    level,
    title: EPISODE_TITLE[episode.kind],
    date: dayText(start.slice(0, 10), thisYear),
    start: start.slice(11),
    end: episode.ongoing ? null : end.slice(0, 10) === start.slice(0, 10) ? end.slice(11) : `${dayText(end.slice(0, 10), thisYear)}, ${end.slice(11)}`,
    duration: episode.ongoing ? `${duration} so far` : duration,
    peak: peak === null ? null : `${peak.toFixed(1)} °C`,
    ongoing: episode.ongoing,
  };
}

/**
 * The episodes of a period in one line, before the list: how many, how long, how hot.
 * "Throttled 15 times, 1 h 40 min in all, up to 85.9 °C · Camera not working once, 8 min". Empty when there are none.
 */
export function episodeSummary(episodes: readonly HealthEpisode[]): string {
  const parts: string[] = [];
  for (const kind of ["slowed", "under-voltage", "camera-offline", "no-data"] as const) {
    const of = episodes.filter((episode) => episode.kind === kind);
    if (of.length === 0) continue;
    const seconds = of.reduce((sum, episode) => sum + (episode.to_s - episode.from_s), 0);
    const peaks = of.flatMap((episode) => (episode.peak_temp_c == null ? [] : [episode.peak_temp_c]));
    const hottest = kind === "slowed" && peaks.length > 0 ? `, up to ${Math.max(...peaks).toFixed(1)} °C` : "";
    const lead = EPISODE_TITLE[kind];
    parts.push(`${lead} ${of.length === 1 ? "once" : `${formatCount(of.length)} times`}, ${minutesText(seconds)}${of.length > 1 ? " in all" : ""}${hottest}`);
  }
  return parts.join(" · ");
}

export interface Tile {
  label: string;
  value: string;
  note?: string;
  /** A word of warning to put by the number, when it calls for one (always with words: colour alone is not enough). */
  flag?: { level: "warning" | "critical"; text: string; href?: string };
}

const percentText = (value: number) => `${value.toFixed(1)}%`;

/** The few numbers over the charts: how hot, how long throttled, how often the camera worked, and how much of the period was watched. */
export function summaryTiles(summary: HealthSummary, history: Pick<SystemHistory, "from_s" | "to_s" | "episodes">): Tile[] {
  const periodMinutes = Math.max(1, Math.round((history.to_s - history.from_s) / 60));
  const recorded = Math.min(100, (100 * summary.minutes) / periodMinutes);
  const heat = summary.temp_c_max == null ? "ok" : temperatureSeverity(summary.temp_c_max);
  const events = history.episodes.filter((episode) => episode.kind === "slowed" || episode.kind === "under-voltage").length;
  return [
    {
      label: "Hottest",
      value: summary.temp_c_max == null ? "–" : `${summary.temp_c_max.toFixed(1)} °C`,
      note: "hottest minute",
      flag:
        heat === "critical"
          ? { level: "critical", text: `past ${HARD_LIMIT_C} °C: throttles hard` }
          : heat === "warning"
            ? { level: "warning", text: "past 80 °C: throttles" }
            : undefined,
    },
    {
      label: "Throttled",
      value: summary.slowed_minutes === 0 ? "never" : minutesText(summary.slowed_minutes * 60),
      note: summary.under_voltage_minutes === 0 ? undefined : `and ${minutesText(summary.under_voltage_minutes * 60)} of weak power`,
      flag:
        summary.slowed_minutes > 0 || summary.under_voltage_minutes > 0
          ? { level: "warning", text: events > 0 ? `See ${events} ${events === 1 ? "event" : "events"}` : "See below", href: "#health-episodes" }
          : undefined,
    },
    {
      label: "Camera uptime",
      value: summary.camera_up_percent == null ? "–" : percentText(summary.camera_up_percent),
      note: "of the monitored time",
    },
    {
      label: "Monitored",
      value: summary.minutes === 0 ? "0 min" : minutesText(summary.minutes * 60),
      note: `${Math.round(recorded)}% of the period`,
    },
  ];
}
