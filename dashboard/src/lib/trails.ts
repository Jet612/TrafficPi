import { orderedClasses } from "./classes";
import { hourLabel, hourRange } from "./format";
import type { LiveLine, LiveZone, Profile, TrailDay, TrailPath } from "./types";

/** A run of hours in a day, as positions in `day.hours` with both ends included. */
export interface HourSpan {
  from: number;
  to: number;
}

export type TrailView = "paths" | "heat";

export const sameSpan = (a: HourSpan, b: HourSpan): boolean => a.from === b.from && a.to === b.to;

/** The run from one hour to another, whichever comes first. */
export function spanBetween(a: number, b: number): HourSpan {
  return { from: Math.min(a, b), to: Math.max(a, b) };
}

export function allDay(day: Pick<TrailDay, "hours">): HourSpan {
  return { from: 0, to: Math.max(0, day.hours.length - 1) };
}

export function isAllDay(day: Pick<TrailDay, "hours">, span: HourSpan): boolean {
  return span.from <= 0 && span.to >= day.hours.length - 1;
}

/** A span kept inside the day's hours: a day has 23 or 25 of them when the clocks change. */
export function clampSpan(span: HourSpan, day: Pick<TrailDay, "hours">): HourSpan {
  const last = Math.max(0, day.hours.length - 1);
  const keep = (index: number) => Math.min(Math.max(index, 0), last);
  return spanBetween(keep(span.from), keep(span.to));
}

/** From the start of a span's first hour to the end of its last, to ask for its paths; null if it is outside the day. */
export function spanRange(day: Pick<TrailDay, "hours">, span: HourSpan): { fromMs: number; toMs: number } | null {
  const first = day.hours[span.from];
  const last = day.hours[span.to];
  return first && last ? { fromMs: first.t, toMs: last.end } : null;
}

/** "All day", "08:00–09:00" for one hour, "07:00–10:00" for a run of three. */
export function spanLabel(day: Pick<TrailDay, "hours">, span: HourSpan): string {
  if (isAllDay(day, span)) return "All day";
  const first = day.hours[span.from];
  const last = day.hours[span.to];
  if (!first || !last) return "";
  if (span.from === span.to) return hourRange(first.hour);
  return `${hourLabel(first.hour)}:00–${hourLabel((last.hour + 1) % 24)}:00`;
}

/**
 * What a button for an hour is called: its time, how many trails began in it, and, for the hour
 * that happens twice when the clocks go back, which time it is.
 */
export function hourButtonLabel(day: Pick<TrailDay, "hours">, index: number, count: number, kinds: ReadonlySet<string>): string {
  const hour = day.hours[index];
  if (!hour) return "";
  const same = day.hours.flatMap((other, at) => (other.hour === hour.hour ? [at] : []));
  const again = same.length < 2 ? "" : same[0] === index ? ", the first time" : ", the second time";
  const things = noun(kinds, count !== 1);
  return `${hourRange(hour.hour)}${again}: ${count === 0 ? `no ${things}` : `${count} ${things}`}`;
}

/** The kinds that appear on a day, in the dashboard's fixed order. */
export function kindsIn(day: Pick<TrailDay, "hours">): string[] {
  const names = new Set<string>();
  for (const hour of day.hours) {
    for (const [kind, n] of Object.entries(hour.by_kind)) if (n > 0) names.add(kind);
  }
  return orderedClasses(names);
}

/** The kinds to show: those that appear on the day and that the visitor has not switched off. */
export function shownKinds(day: Pick<TrailDay, "hours">, hidden: ReadonlySet<string>): Set<string> {
  return new Set(kindsIn(day).filter((kind) => !hidden.has(kind)));
}

/** How many trails began in each hour, counting only these kinds. */
export function hourCounts(day: Pick<TrailDay, "hours">, kinds: ReadonlySet<string>): number[] {
  return day.hours.map((hour) =>
    Object.entries(hour.by_kind).reduce((sum, [kind, n]) => (kinds.has(kind) ? sum + n : sum), 0),
  );
}

/** How many trails began in each kind, over the whole day or just a span of it. */
export function kindTotals(day: Pick<TrailDay, "hours">, span: HourSpan = allDay(day)): Map<string, number> {
  const totals = new Map<string, number>();
  for (const hour of day.hours.slice(Math.max(0, span.from), span.to + 1)) {
    for (const [kind, n] of Object.entries(hour.by_kind)) totals.set(kind, (totals.get(kind) ?? 0) + n);
  }
  return totals;
}

export function countIn(counts: readonly number[], span: HourSpan): number {
  let sum = 0;
  for (let index = Math.max(0, span.from); index <= span.to && index < counts.length; index++) sum += counts[index] ?? 0;
  return sum;
}

/** The hours worth stopping at when a day is played: those with something to show. */
export function playlist(counts: readonly number[]): number[] {
  return counts.flatMap((count, index) => (count > 0 ? [index] : []));
}

/** The hour after `current` in a playlist (the first when nothing is playing yet), or null at the end. */
export function nextHour(list: readonly number[], current: number | null): number | null {
  if (current === null) return list[0] ?? null;
  return list.find((index) => index > current) ?? null;
}

/** The paths of the kinds that are switched on. */
export function pathsOfKinds(paths: readonly TrailPath[], kinds: ReadonlySet<string>): TrailPath[] {
  return paths.filter((path) => kinds.has(path.label));
}

/** "vehicle", "vehicles", "person", "people", or "road users" when both are shown. */
export function noun(kinds: ReadonlySet<string>, plural: boolean): string {
  const people = kinds.has("person");
  const others = [...kinds].some((kind) => kind !== "person");
  if (people && others) return plural ? "road users" : "road user";
  if (people) return plural ? "people" : "person";
  return plural ? "vehicles" : "vehicle";
}

/** "1 vehicle", "312 vehicles", and, when fewer paths are drawn than there were, how many. */
export function countText(count: number, drawn: number, kinds: ReadonlySet<string>, format: (n: number) => string): string {
  const base = `${format(count)} ${noun(kinds, count !== 1)}`;
  return drawn < count ? `${base} (drawing an even sample of ${format(drawn)})` : base;
}

/** At most `max` of the items, spread evenly through the list (the first, then every n-th). */
export function evenSample<T>(items: readonly T[], max: number): T[] {
  if (items.length <= max) return [...items];
  if (max <= 0) return [];
  const step = items.length / max;
  return Array.from({ length: max }, (_, index) => items[Math.floor(index * step)]!);
}

/** The profile's lines and zones in the shape the overlay draws them. */
export function profileScene(profile: Profile): { lines: LiveLine[]; zones: LiveZone[] } {
  return {
    lines: profile.lines.map((line) => ({ id: line.id, name: line.name, a: line.a, b: line.b })),
    zones: [
      ...profile.detection_zones.map((zone): LiveZone => ({ id: zone.id, name: zone.name, kind: "detection", points: zone.points })),
      ...profile.exclusion_zones.map((zone): LiveZone => ({ id: zone.id, name: zone.name, kind: "exclusion", points: zone.points })),
    ],
  };
}

// --- days -------------------------------------------------------------------------------------------

const DAY_PATTERN = /^(\d{4})-(\d{2})-(\d{2})$/;
const DAY_MS = 86_400_000;

/** Midnight UTC of a "YYYY-MM-DD" date, or null if the text is not a real date. */
function dayStart(day: string): number | null {
  const match = DAY_PATTERN.exec(day);
  if (!match) return null;
  const [year, month, date] = [Number(match[1]), Number(match[2]), Number(match[3])];
  const ms = Date.UTC(year, month - 1, date);
  const back = new Date(ms);
  return back.getUTCFullYear() === year && back.getUTCMonth() === month - 1 && back.getUTCDate() === date ? ms : null;
}

export const isDay = (text: string): boolean => dayStart(text) !== null;

/** The date `by` days from this one (negative for earlier), or null if the text is not a date. */
export function shiftDay(day: string, by: number): string | null {
  const start = dayStart(day);
  return start === null ? null : new Date(start + by * DAY_MS).toISOString().slice(0, 10);
}

/** "Friday 9 October 2026": a date is the same calendar day wherever the browser is. */
export function dayTitle(day: string): string {
  const start = dayStart(day);
  if (start === null) return day;
  return new Intl.DateTimeFormat("en-GB", { weekday: "long", day: "numeric", month: "long", year: "numeric", timeZone: "UTC" })
    .format(new Date(start))
    .replace(",", "");
}

/** The name of a saved picture: what it shows, which day, and which hours unless it is all of them. */
export function pictureFileName(day: Pick<TrailDay, "day" | "hours">, span: HourSpan, view: TrailView): string {
  const first = day.hours[span.from];
  const last = day.hours[span.to];
  const hours =
    isAllDay(day, span) || !first || !last
      ? ""
      : `-${hourLabel(first.hour)}00-${hourLabel((last.hour + 1) % 24)}00`;
  return `trafficpi-${view === "heat" ? "heat-map" : "paths"}-${day.day}${hours}.png`;
}
