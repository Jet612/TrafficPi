import { formatDateTime } from "./format";
import type { StatusLevel } from "@/components/ui/status";
import type { StopHoursConfig, StopHoursDay, StopHoursStatus } from "./types";

/** The server's own limits (backend core/stop_hours.py): the form says so before it is asked. */
export const MIN_MARGIN = -120;
export const MAX_MARGIN = 180;
export const MAX_RANGES = 6;
/** How many days ahead the server looks for the next change (backend core/stop_hours.py, LOOK_AHEAD_DAYS). */
const LOOK_AHEAD_DAYS = 9;

/** How far from midday (in minutes) the sun may be highest by the camera's clock before the longitude is doubted. */
const NOON_DOUBT_MIN = 180;
const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;

export interface RangeDraft {
  start: string;
  end: string;
}

/** The form as it holds the setting: what is typed in a box is text until it has been checked. */
export interface StopHoursDraft {
  daylight: boolean;
  latitude: string;
  longitude: string;
  before: string;
  after: string;
  clock: boolean;
  ranges: RangeDraft[];
  days: number[];
}

export function toDraft(config: StopHoursConfig): StopHoursDraft {
  return {
    daylight: config.daylight,
    latitude: config.latitude === null ? "" : String(config.latitude),
    longitude: config.longitude === null ? "" : String(config.longitude),
    before: String(config.before_sunrise_min),
    after: String(config.after_sunset_min),
    clock: config.clock,
    ranges: config.ranges.map((range) => ({ start: range.start, end: range.end })),
    days: [...config.days],
  };
}

/** A coordinate typed in decimal degrees ("40.71", "-74.006"), if it is one within ±`limit`; null otherwise. */
export function parseCoordinate(text: string, limit: number): number | null {
  const trimmed = text.trim();
  if (!/^[+-]?\d{1,3}(\.\d{1,8})?$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return Math.abs(value) <= limit ? value : null;
}

/** Minutes typed, if they are a whole number the server will take (negative: the other side of the sun). */
export function parseMargin(text: string): number | null {
  const trimmed = text.trim();
  if (!/^[+-]?\d{1,3}$/.test(trimmed)) return null;
  const value = Number(trimmed);
  return value >= MIN_MARGIN && value <= MAX_MARGIN ? value : null;
}

export type DraftResult = { ok: true; config: StopHoursConfig } | { ok: false; problem: string };

/** The setting to send, or what is wrong with the form, in words. (The server checks all of it again.) */
export function fromDraft(draft: StopHoursDraft): DraftResult {
  const problem = (text: string): DraftResult => ({ ok: false, problem: text });
  const latText = draft.latitude.trim();
  const lonText = draft.longitude.trim();
  let latitude: number | null = null;
  let longitude: number | null = null;
  if (latText !== "" || lonText !== "") {
    if (latText === "" || lonText === "") return problem("Enter both the latitude and the longitude, or leave both empty.");
    latitude = parseCoordinate(latText, 90);
    if (latitude === null) return problem("The latitude must be a number from -90 to 90, like 40.71.");
    longitude = parseCoordinate(lonText, 180);
    if (longitude === null) return problem("The longitude must be a number from -180 to 180, like -74.01.");
  }
  if (draft.daylight && latitude === null) return problem("Enter the camera's latitude and longitude to judge only in daylight.");
  const before = parseMargin(draft.before);
  if (before === null) return problem(`The start must be a whole number of minutes from ${MIN_MARGIN} to ${MAX_MARGIN} before sunrise (negative starts after it).`);
  const after = parseMargin(draft.after);
  if (after === null) return problem(`The end must be a whole number of minutes from ${MIN_MARGIN} to ${MAX_MARGIN} after sunset (negative stops before it).`);
  if (draft.ranges.length === 0) return problem("Give at least one range of hours.");
  if (draft.ranges.length > MAX_RANGES) return problem(`Give at most ${MAX_RANGES} ranges of hours.`);
  for (const [index, range] of draft.ranges.entries()) {
    const which = draft.ranges.length > 1 ? ` of range ${index + 1}` : "";
    if (!TIME.test(range.start) || !TIME.test(range.end)) return problem(`Fill in both times${which}.`);
    if (range.start === range.end) {
      return problem(draft.ranges.length > 1 ? `Range ${index + 1} starts and ends at the same time.` : "The range starts and ends at the same time.");
    }
  }
  if (draft.days.length === 0) return problem("Pick at least one day of the week.");
  return {
    ok: true,
    config: {
      daylight: draft.daylight,
      latitude,
      longitude,
      before_sunrise_min: before,
      after_sunset_min: after,
      clock: draft.clock,
      ranges: draft.ranges.map((range) => ({ start: range.start, end: range.end })),
      days: [...draft.days].sort((a, b) => a - b),
    },
  };
}

function canonical(config: StopHoursConfig): string {
  return JSON.stringify([
    config.daylight,
    config.latitude,
    config.longitude,
    config.before_sunrise_min,
    config.after_sunset_min,
    config.clock,
    config.ranges.map((range) => [range.start, range.end]),
    [...config.days].sort((a, b) => a - b),
  ]);
}

/** Whether the form differs from what is saved (spaces round a typed value do not count). */
export function isChanged(draft: StopHoursDraft, saved: StopHoursConfig): boolean {
  const result = fromDraft(draft);
  return !result.ok || canonical(result.config) !== canonical(saved);
}

function addHour(time: string): string {
  const [hours, minutes] = time.split(":").map(Number);
  return `${String(((hours ?? 0) + 1) % 24).padStart(2, "0")}:${String(minutes ?? 0).padStart(2, "0")}`;
}

/** The ranges with one more: an hour long, starting where the last one ends. It may run through midnight. */
export function addRange(ranges: readonly RangeDraft[]): RangeDraft[] {
  if (ranges.length >= MAX_RANGES) return [...ranges];
  const last = ranges[ranges.length - 1];
  const start = last !== undefined && TIME.test(last.end) ? last.end : "09:00";
  return [...ranges, { start, end: addHour(start) }];
}

/** The ranges without the one at `index`; one is always left. */
export function removeRange(ranges: readonly RangeDraft[], index: number): RangeDraft[] {
  if (ranges.length <= 1 || index < 0 || index >= ranges.length) return [...ranges];
  return ranges.filter((_, i) => i !== index);
}

// --- in words ---------------------------------------------------------------------------------

/** "2026-10-09" for the date of an instant by a zone's clock. */
function dateKey(ms: number, timeZone: string): string {
  return formatDateTime(ms, timeZone).slice(0, 10);
}

function nextDateKey(key: string): string {
  const [year, month, day] = key.split("-").map(Number);
  return new Date(Date.UTC(year ?? 1970, (month ?? 1) - 1, (day ?? 1) + 1)).toISOString().slice(0, 10);
}

/** "Fri 9 Oct": a date, written as the year-month-day it is, with no zone in it. */
function dateWords(date: string): string {
  const [year, month, day] = date.split("-").map(Number);
  const at = new Date(Date.UTC(year ?? 1970, (month ?? 1) - 1, day ?? 1));
  const part = (options: Intl.DateTimeFormatOptions) => new Intl.DateTimeFormat("en-GB", { timeZone: "UTC", ...options }).format(at);
  return `${part({ weekday: "short" })} ${part({ day: "numeric" })} ${part({ month: "short" })}`;
}

/** An instant as "today at 18:55", "tomorrow at 06:32" or "Mon 12 Oct at 07:00", by the camera's clock. */
export function whenText(ms: number, nowMs: number, timeZone: string): string {
  const key = dateKey(ms, timeZone);
  const today = dateKey(nowMs, timeZone);
  const clock = formatDateTime(ms, timeZone).slice(11);
  if (key === today) return `today at ${clock}`;
  if (key === nextDateKey(today)) return `tomorrow at ${clock}`;
  return `${dateWords(key)} at ${clock}`;
}

function clockOf(ms: number, timeZone: string): string {
  return formatDateTime(ms, timeZone).slice(11);
}

/** What the sun does today at the place, in a sentence, and the window around it. */
export function dayText(day: StopHoursDay, timeZone: string): string {
  const today = `Today (${dateWords(day.date)})`;
  if (day.polar === "day") return `${today} the sun does not set at this place, so all of it is daylight.`;
  if (day.polar === "night") return `${today} the sun does not rise at this place, so there is no daylight.`;
  if (day.sunrise === null || day.sunset === null) return "";
  const sun = `${today} the sun rises at ${clockOf(day.sunrise, timeZone)} and sets at ${clockOf(day.sunset, timeZone)}`;
  if (day.start === null || day.end === null) return `${sun}; the margins leave no daylight window.`;
  return `${sun}, so the daylight window is ${clockOf(day.start, timeZone)} to ${clockOf(day.end, timeZone)}.`;
}

/** A warning, when the sun is highest at this place hours from midday on the camera's clock: nearly always a longitude with the wrong sign. */
export function noonWarning(day: StopHoursDay): string | null {
  if (Math.abs(day.noon_offset_min) <= NOON_DOUBT_MIN) return null;
  const minutes = (((720 + day.noon_offset_min) % 1440) + 1440) % 1440;
  const clock = `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
  return (
    `At this place the sun is highest at ${clock} by the camera's clock, far from midday. Check the signs: north is positive and south negative for the latitude, ` +
    "east is positive and west negative for the longitude, so a place in the Americas has a negative longitude."
  );
}

/** What a save says: that it is saved, and whether the vision service already uses it. */
export function savedText(visionNotified: boolean): string {
  return visionNotified ? "Saved. The vision service uses it at once." : "Saved. The vision service could not be reached, so it will use it when it starts.";
}

/** Whether stop signs are being judged now, and when that changes, in a sentence. */
export function statusText(status: StopHoursStatus): { level: StatusLevel; text: string } {
  if (!status.limited) return { level: "neutral", text: "Every pass is judged, at every hour." };
  const when = status.next_change === null ? null : whenText(status.next_change, status.now, status.timezone);
  if (status.judging) {
    return { level: "good", text: when === null ? "Stop signs are being judged now." : `Stop signs are being judged now. Judging pauses ${when}.` };
  }
  return {
    level: "neutral",
    text: when === null ? `Stop signs are not being judged now. Judging does not resume in the next ${LOOK_AHEAD_DAYS} days.` : `Stop signs are not being judged now. Judging resumes ${when}.`,
  };
}

/** A line for the pages that show the passes: which hours they are judged in, and where that stands now. Null when every hour counts. */
export function noteText(status: StopHoursStatus): string | null {
  if (!status.limited) return null;
  const when = status.next_change === null ? null : whenText(status.next_change, status.now, status.timezone);
  const only = `Stop signs are judged only ${status.description}.`;
  if (status.judging) return when === null ? only : `${only} Judging pauses ${when}.`;
  return when === null ? `${only} They are paused now.` : `${only} They are paused now and resume ${when}.`;
}
