import { CLASS_ORDER, classSlot } from "./classes";
import { hourLabel } from "./format";
import type { SeriesPoint, TodaySummary } from "./types";

export interface HourRow {
  t: number; // start of the hour, ms since the epoch
  hour: number; // local hour of day
  label: string; // "07"
  current: boolean; // the hour in progress
  total: number;
  byClass: Record<string, number>;
}

/**
 * One row per hour of the local day: the hours come from the day's summary (so a day with a
 * daylight-saving change has 23 or 25), the split by class from the series. Counts that the two
 * answers disagree on (they are fetched separately) are resolved in favour of the split.
 */
export function buildHourlyRows(
  today: TodaySummary,
  series: SeriesPoint[] | null,
  nowMs: number,
): HourRow[] {
  const byStart = new Map((series ?? []).map((point) => [point.t, point]));
  return today.hourly.map((hour, index) => {
    const next = today.hourly[index + 1]?.t ?? hour.t + 3_600_000;
    const point = byStart.get(hour.t);
    const byClass = point ? { ...point.by_class } : {};
    const split = Object.values(byClass).reduce((sum, n) => sum + n, 0);
    return {
      t: hour.t,
      hour: hour.hour,
      label: hourLabel(hour.hour),
      current: hour.t <= nowMs && nowMs < next,
      total: point ? split : hour.count,
      byClass,
    };
  });
}

/** The busiest hour, or null when nothing was counted. Ties go to the earlier hour. */
export function peakRow(rows: HourRow[]): HourRow | null {
  let best: HourRow | null = null;
  for (const row of rows) {
    if (row.total > 0 && (best === null || row.total > best.total)) best = row;
  }
  return best;
}

/** Series keys of a stacked column: the known classes in order, then one shared "other". */
export function stackKeys(rows: HourRow[]): string[] {
  const present = new Set(rows.flatMap((row) => Object.keys(row.byClass).filter((k) => row.byClass[k]! > 0)));
  const known = CLASS_ORDER.filter((name) => present.has(name));
  const hasOther = [...present].some((name) => classSlot(name) === null);
  return hasOther ? [...known, "other"] : [...known];
}

/** A row's count for a stack key; "other" adds up every class the dashboard does not know. */
export function countFor(row: HourRow, key: string): number {
  if (key !== "other") return row.byClass[key] ?? 0;
  return Object.entries(row.byClass)
    .filter(([name]) => classSlot(name) === null)
    .reduce((sum, [, n]) => sum + n, 0);
}

/** Round a chart's top up to a tidy value, so the axis ends on a labelled gridline. */
export function niceMax(value: number): number {
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 4, 5, 10]) {
    const candidate = step * magnitude;
    if (candidate >= value) return candidate;
  }
  return 10 * magnitude;
}

/** Gridline values from 0 to `max` (as returned by niceMax): whole numbers, four or five intervals. */
export function axisTicks(max: number): number[] {
  if (max <= 5) return Array.from({ length: Math.floor(max) + 1 }, (_, i) => i);
  for (let power = 1; power <= max; power *= 10) {
    for (const factor of [1, 2, 2.5, 5]) {
      const step = factor * power;
      if (Number.isInteger(step) && max % step === 0 && max / step <= 5) {
        return Array.from({ length: max / step + 1 }, (_, i) => i * step);
      }
    }
  }
  return [0, max];
}
