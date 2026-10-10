import { classColor, classLabel, orderedClasses } from "./classes";
import { dayText, hourLabel, hourRange, shortDayText } from "./format";
import { SIZE_METRES, inUnits } from "./speed";
import { OUTCOMES, OUTCOME_LABEL } from "./stops";
import type { StopOutcome, StopStats, TrafficStats } from "./types";

/** How many vehicles had each outcome (passes called false alarms are not in it): the server's `OutcomeCounts`. */
export type OutcomeCounts = StopStats["totals"];

// --- the period ----------------------------------------------------------------------------------

export type DataPeriod = "today" | "week" | "month" | "quarter" | "all";

/** The stretches to choose from, as local days ending today; 0 asks the server for everything since the first one. */
export const DATA_PERIODS: readonly { value: DataPeriod; label: string; days: number; before: string | null }[] = [
  { value: "today", label: "Today", days: 1, before: "yesterday" },
  { value: "week", label: "7 days", days: 7, before: "the 7 days before" },
  { value: "month", label: "30 days", days: 30, before: "the 30 days before" },
  { value: "quarter", label: "90 days", days: 90, before: "the 90 days before" },
  { value: "all", label: "All time", days: 0, before: null },
];

export function periodDays(period: DataPeriod): number {
  return DATA_PERIODS.find((option) => option.value === period)?.days ?? 7;
}

/** What the last period is compared with, in words ("yesterday"); null for all time, which has nothing before it. */
export function comparedWith(period: DataPeriod): string | null {
  return DATA_PERIODS.find((option) => option.value === period)?.before ?? null;
}

// --- shares --------------------------------------------------------------------------------------

/** A highlight about one hour or one kind needs this many vehicles, or one out of one would be "100%". */
export const MIN_SAMPLE = 5;

/** Below this many vehicles in all, percentages are said to move a lot. */
export const SMALL_SAMPLE = 20;

export const totalOf = (counts: OutcomeCounts): number => counts.stopped + counts.rolling + counts.did_not_stop + counts.indeterminate;

/** Vehicles that did not stop or rolled through. */
export const violationsOf = (counts: OutcomeCounts): number => counts.rolling + counts.did_not_stop;

/** The vehicles whose pass could be judged: all but those that could not be told. */
export const toldOf = (counts: OutcomeCounts): number => counts.stopped + counts.rolling + counts.did_not_stop;

/**
 * Whole-number percentages of the total that add up to 100: each share is rounded down, and the
 * points left over go to the largest remainders (the earlier outcome first on a tie). All 0 when
 * there is nothing to share.
 */
export function sharesOf(counts: OutcomeCounts): Record<StopOutcome, number> {
  const total = totalOf(counts);
  const result: Record<StopOutcome, number> = { stopped: 0, rolling: 0, did_not_stop: 0, indeterminate: 0 };
  if (total === 0) return result;
  const exact = OUTCOMES.map((outcome) => ({ outcome, value: (counts[outcome] / total) * 100 }));
  for (const { outcome, value } of exact) result[outcome] = Math.floor(value + 1e-9);
  let left = 100 - OUTCOMES.reduce((sum, outcome) => sum + result[outcome], 0);
  const byRemainder = [...exact].sort((a, b) => b.value - Math.floor(b.value + 1e-9) - (a.value - Math.floor(a.value + 1e-9)));
  for (const { outcome } of byRemainder) {
    if (left <= 0) break;
    if (counts[outcome] > 0) {
      result[outcome] += 1;
      left -= 1;
    }
  }
  return result;
}

/** How many points a share moved since the period before, as the page shows both; null if either has no vehicles. */
export function changeInPoints(current: OutcomeCounts, before: OutcomeCounts | null, outcome: StopOutcome): number | null {
  if (before === null || totalOf(current) === 0 || totalOf(before) === 0) return null;
  return sharesOf(current)[outcome] - sharesOf(before)[outcome];
}

export type Tone = "good" | "bad" | "neutral";

/** Whether a move of a share is welcome: more stops are, more rolls and misses are not, and what could not be told is neither. */
export function changeTone(outcome: StopOutcome, points: number): Tone {
  if (points === 0 || outcome === "indeterminate") return "neutral";
  const up = points > 0;
  return (outcome === "stopped") === up ? "good" : "bad";
}

/** "up 4 points", "down 1 point", "no change". */
export function pointsText(points: number): string {
  if (points === 0) return "no change";
  const size = Math.abs(points);
  return `${points > 0 ? "up" : "down"} ${size} ${size === 1 ? "point" : "points"}`;
}

/** Of the vehicles whose pass could be judged, how many percent stopped; null if none could be. */
export function stopRate(counts: OutcomeCounts): { percent: number; of: number } | null {
  const of = toldOf(counts);
  return of === 0 ? null : { percent: Math.round((counts.stopped / of) * 100), of };
}

/** The stop rate of a column of a chart or a row of its table, as text: "80%", or "–" if no vehicle in it could be judged. */
export function stopRateText(values: Readonly<Record<string, number>>): string {
  const stopped = values.stopped ?? 0;
  const told = stopped + (values.rolling ?? 0) + (values.did_not_stop ?? 0);
  return told === 0 ? "–" : `${Math.round((stopped / told) * 100)}%`;
}

// --- highlights ----------------------------------------------------------------------------------

/** The item with the largest value; the earlier one on a tie; null when there is nothing above zero. */
export function peak<T>(items: readonly T[], value: (item: T) => number): { item: T; value: number } | null {
  let best: { item: T; value: number } | null = null;
  for (const item of items) {
    const v = value(item);
    if (v > 0 && (best === null || v > best.value)) best = { item, value: v };
  }
  return best;
}

interface Rated<T> {
  item: T;
  percent: number;
  of: number;
}

/**
 * The items with the lowest and the highest stop rate, among those with at least `MIN_SAMPLE` vehicles
 * that could be judged. Needs two such items, or there is nothing to compare; `highest` is null when
 * every one of them has the same rate (the earlier item wins a tie).
 */
export function rateExtremes<T extends OutcomeCounts>(items: readonly T[]): { lowest: Rated<T>; highest: Rated<T> | null } | null {
  const rated: Rated<T>[] = [];
  for (const item of items) {
    const rate = stopRate(item);
    if (rate !== null && rate.of >= MIN_SAMPLE) rated.push({ item, ...rate });
  }
  if (rated.length < 2) return null;
  let lowest = rated[0]!;
  let highest = rated[0]!;
  for (const entry of rated) {
    if (entry.percent < lowest.percent) lowest = entry;
    if (entry.percent > highest.percent) highest = entry;
  }
  return { lowest, highest: highest.percent === lowest.percent ? null : highest };
}

// --- speed in words ------------------------------------------------------------------------------

/** A speed in the vehicle's own sizes a second as a whole number of mph or km/h, for a car (`SIZE_METRES`, which is not a car's length). */
export function approximateSpeed(sizesPerSecond: number, units: "mph" | "kmh"): number {
  return Math.round(inUnits(sizesPerSecond * SIZE_METRES, units));
}

/** "about 4 mph", "about 6 km/h", "almost still". */
export function speedText(sizesPerSecond: number, units: "mph" | "kmh"): string {
  const speed = approximateSpeed(sizesPerSecond, units);
  return speed < 1 ? "almost still" : `about ${speed} ${units === "mph" ? "mph" : "km/h"}`;
}

// --- days ----------------------------------------------------------------------------------------

/** "Oct 9": the label of a column of the daily chart. */
export function dayLabel(date: string): string {
  return shortDayText(date);
}

/** "Fri, Oct 9, 2026": a day in full (a day of a chart or a table says its year, which the chart's own range does not). */
export function dayTitle(date: string): string {
  return dayText(date, -1);
}

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] as const;

/** 0 is Monday, as the server counts them. */
export const weekdayName = (weekday: number): string => WEEKDAYS[weekday] ?? String(weekday);

/** A day with its year when the span it belongs to crosses years: "Oct 9" or "Oct 9, 2026". */
const spanDay = (date: string, withYear: boolean): string => (withYear ? `${dayLabel(date)}, ${date.slice(0, 4)}` : dayLabel(date));

/** "Oct 3 – Oct 9", or "Oct 9" for a single day; the years are said when the days are not in one: "Dec 28, 2026 – Jan 3, 2027". */
export function rangeText(firstDate: string | undefined, lastDate: string | undefined): string {
  if (!firstDate || !lastDate) return "";
  if (firstDate === lastDate) return dayLabel(firstDate);
  const years = firstDate.slice(0, 4) !== lastDate.slice(0, 4);
  return `${spanDay(firstDate, years)} – ${spanDay(lastDate, years)}`;
}

/** The days a period covers, said once for the whole page: "Oct 3 – Oct 9", or "since Oct 3" for all time ("since Oct 3, 2025" from an earlier year). */
export function periodText(byDay: readonly { date: string }[], allTime: boolean): string {
  const first = byDay[0]?.date;
  const last = byDay.at(-1)?.date;
  if (!allTime) return rangeText(first, last);
  return first && last ? `since ${spanDay(first, first.slice(0, 4) !== last.slice(0, 4))}` : "";
}

/** How many of the days have anything in them: a busiest day means something only from two of them. */
export function daysWithData(byDay: readonly { total: number }[]): number {
  return byDay.filter((day) => day.total > 0).length;
}

/**
 * The rows from the first one that has anything in it, so that a record that began this week does not
 * draw a chart that is empty until then. Everything is kept when none has anything (there is nothing to
 * cut to) or when the first has.
 */
export function trimEmptyLead<T extends { total: number }>(rows: readonly T[]): T[] {
  const first = rows.findIndex((row) => row.total > 0);
  return first <= 0 ? [...rows] : rows.slice(first);
}

/**
 * The average number counted on a day, over the days that are over: today is not finished, so it is
 * left out when there are other days. Null when there is no finished day, or nothing was counted.
 */
export function averagePerFinishedDay(byDay: readonly { total: number }[]): number | null {
  const finished = byDay.length > 1 ? byDay.slice(0, -1) : [];
  const sum = finished.reduce((total, day) => total + day.total, 0);
  return finished.length === 0 || sum === 0 ? null : Math.round(sum / finished.length);
}

// --- chart rows ----------------------------------------------------------------------------------

/** One stack of a chart: the colour is the mark's, and the words are on the legend, the tooltip and the table. */
export interface ChartSeries {
  key: string;
  label: string;
  color: string;
}

/** One column of a stacked chart or one row of its table. */
export interface ChartRow {
  id: string;
  /** On the axis. */
  label: string;
  /** In the tooltip and the table. */
  title: string;
  values: Record<string, number>;
  total: number;
}

/** The status colours are reserved for these four, which always come with an icon and words too. */
export const OUTCOME_COLOR: Record<StopOutcome, string> = {
  stopped: "var(--good)",
  rolling: "var(--warning)",
  did_not_stop: "var(--critical)",
  indeterminate: "var(--series-other)",
};

/** The four outcomes in the order they are stacked: the good one on the baseline, the unknown on top. */
export const OUTCOME_SERIES: readonly ChartSeries[] = OUTCOMES.map((outcome) => ({
  key: outcome,
  label: OUTCOME_LABEL[outcome],
  color: OUTCOME_COLOR[outcome],
}));

type Outcomes = Pick<OutcomeCounts, StopOutcome>;

const outcomeValues = (counts: Outcomes): Record<string, number> => ({
  stopped: counts.stopped,
  rolling: counts.rolling,
  did_not_stop: counts.did_not_stop,
  indeterminate: counts.indeterminate,
});

export function outcomeHourRows(byHour: StopStats["by_hour"]): ChartRow[] {
  return byHour.map((row) => ({
    id: `h${row.hour}`,
    label: hourLabel(row.hour),
    title: hourRange(row.hour),
    values: outcomeValues(row),
    total: totalOf(row),
  }));
}

export function outcomeDayRows(byDay: StopStats["by_day"]): ChartRow[] {
  return byDay.map((row) => ({
    id: row.date,
    label: dayLabel(row.date),
    title: dayTitle(row.date),
    values: outcomeValues(row),
    total: totalOf(row),
  }));
}

/** The kinds counted in the rows, the known ones in their usual order and then the others by name, each as a series. */
export function classSeries(rows: readonly { by_class: Record<string, number> }[]): ChartSeries[] {
  const present = new Set(rows.flatMap((row) => Object.entries(row.by_class).filter(([, n]) => n > 0).map(([name]) => name)));
  return orderedClasses(present).map((name) => ({ key: name, label: classLabel(name), color: classColor(name) }));
}

export function trafficHourRows(byHour: TrafficStats["by_hour"]): ChartRow[] {
  return byHour.map((row) => ({ id: `h${row.hour}`, label: hourLabel(row.hour), title: hourRange(row.hour), values: row.by_class, total: row.total }));
}

export function trafficDayRows(byDay: TrafficStats["by_day"]): ChartRow[] {
  return byDay.map((row) => ({ id: row.date, label: dayLabel(row.date), title: dayTitle(row.date), values: row.by_class, total: row.total }));
}

/** The same rows with each value as a percentage of its row's total (all 0 for an empty row), for the "share" view. */
export function asShares(rows: readonly ChartRow[]): ChartRow[] {
  return rows.map((row) => ({
    ...row,
    values: Object.fromEntries(Object.entries(row.values).map(([key, n]) => [key, row.total === 0 ? 0 : (n / row.total) * 100])),
    total: row.total === 0 ? 0 : 100,
  }));
}
