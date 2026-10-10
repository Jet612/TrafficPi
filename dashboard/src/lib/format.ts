const counts = new Intl.NumberFormat("en-US");

/** 1234 -> "1,234". */
export function formatCount(n: number): string {
  return counts.format(n);
}

/** Seconds -> "45 s", "5 m 12 s", "2 h 05 m", "3 d 4 h". */
export function formatDuration(totalSeconds: number): string {
  const s = Math.max(0, Math.floor(totalSeconds));
  const days = Math.floor(s / 86400);
  const hours = Math.floor((s % 86400) / 3600);
  const minutes = Math.floor((s % 3600) / 60);
  const seconds = s % 60;
  if (days > 0) return `${days} d ${hours} h`;
  if (hours > 0) return `${hours} h ${String(minutes).padStart(2, "0")} m`;
  if (minutes > 0) return `${minutes} m ${String(seconds).padStart(2, "0")} s`;
  return `${seconds} s`;
}

/** The clock time of an instant in a time zone, 24-hour: "14:05:09". */
export function formatClock(ms: number, timeZone: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: safeZone(timeZone),
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  }).format(new Date(ms));
}

/** The date and time of an instant in a time zone, 24-hour and unambiguous: "2026-10-09 00:16". */
export function formatDateTime(ms: number, timeZone: string): string {
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", {
      timeZone: safeZone(timeZone),
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23",
    })
      .formatToParts(new Date(ms))
      .map((part) => [part.type, part.value]),
  );
  return `${parts.year}-${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`;
}

/** The date and time down to the second, as for an event that happened at that moment: "2026-10-09 14:05:09". */
export function formatDateTimeSeconds(ms: number, timeZone: string): string {
  return `${formatDateTime(ms, timeZone)}:${formatClock(ms, timeZone).slice(-2)}`;
}

// --- dates and times, as the dashboard writes them for people -----------------------------------------
//
// One style everywhere. A day is "Fri, Oct 9" (and "Fri, Oct 9, 2025" when it is not in this year), a
// clock time is the 24-hour "22:07", with seconds only where they matter (the live feed, a tooltip), and
// something that happened a moment ago is "2 min ago". The year-month-day form above is for working a
// time out (it is what the sums slice up), not for reading.

const DAY_FORMAT = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", weekday: "short", month: "short", day: "numeric" });
const DATE_PARTS = /^(\d{4})-(\d{2})-(\d{2})$/;

/** A local date like "2026-10-09" as an instant at noon UTC, so that no zone can move it to another day. */
function noonOf(date: string): Date | null {
  const match = DATE_PARTS.exec(date);
  return match ? new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), 12)) : null;
}

/** "2026-10-09": the date of an instant on the wall clock of a zone. */
export function localDate(ms: number, timeZone: string): string {
  return formatDateTime(ms, timeZone).slice(0, 10);
}

/** The date after, or before, a local date ("2026-10-09" and 1 give "2026-10-10"); the date itself if it is not one. */
export function shiftDate(date: string, days: number): string {
  const noon = noonOf(date);
  if (noon === null) return date;
  noon.setUTCDate(noon.getUTCDate() + days);
  return noon.toISOString().slice(0, 10);
}

/** "Fri, Oct 9" for a local date; "Fri, Oct 9, 2025" when the date is not in `thisYear`. */
export function dayText(date: string, thisYear: number): string {
  const noon = noonOf(date);
  if (noon === null) return date;
  const text = DAY_FORMAT.format(noon);
  return Number(date.slice(0, 4)) === thisYear ? text : `${text}, ${date.slice(0, 4)}`;
}

/** The year it is now in a zone, which decides whether a date needs its year. */
export function yearIn(nowMs: number, timeZone: string): number {
  return Number(localDate(nowMs, timeZone).slice(0, 4));
}

const SHORT_DAY_FORMAT = new Intl.DateTimeFormat("en-US", { timeZone: "UTC", month: "short", day: "numeric" });

/** "Oct 9": a day short enough for the axis of a chart. */
export function shortDayText(date: string): string {
  const noon = noonOf(date);
  return noon === null ? date : SHORT_DAY_FORMAT.format(noon);
}

/** "22:07": a clock time, 24-hour, no seconds. */
export function timeText(ms: number, timeZone: string): string {
  return formatClock(ms, timeZone).slice(0, 5);
}

/** "Fri, Oct 9, 2026, 22:07:58": all of an instant, for a tooltip or a spoken name. */
export function fullTimeText(ms: number, timeZone: string): string {
  // No year is ever "this year" here (-1), so the year is always said.
  return `${dayText(localDate(ms, timeZone), -1)}, ${formatClock(ms, timeZone)}`;
}

/**
 * When something happened, as short as it can be said: "just now", "2 min ago" for under an hour,
 * then "22:07" for earlier today, "Yesterday, 22:07", and "Fri, Oct 9, 22:07" for anything older.
 */
export function momentText(ms: number, timeZone: string, nowMs: number): string {
  const age = nowMs - ms;
  if (age < 60_000) return "just now";
  if (age < 3_600_000) return `${Math.round(age / 60_000)} min ago`;
  const date = localDate(ms, timeZone);
  const today = localDate(nowMs, timeZone);
  const clock = timeText(ms, timeZone);
  if (date === today) return clock;
  if (date === shiftDate(today, -1)) return `Yesterday, ${clock}`;
  return `${dayText(date, yearIn(nowMs, timeZone))}, ${clock}`;
}

/**
 * A moment in a sentence, after a verb: "just now", "2 min ago", "at 19:26", "yesterday at 19:26", "on Fri, Oct 9 at 19:26"
 * ("the test mail was sent at 19:26").
 */
export function atText(ms: number, timeZone: string, nowMs: number): string {
  const age = nowMs - ms;
  if (age < 60_000) return "just now";
  if (age < 3_600_000) return `${Math.round(age / 60_000)} min ago`;
  const date = localDate(ms, timeZone);
  const today = localDate(nowMs, timeZone);
  const clock = timeText(ms, timeZone);
  if (date === today) return `at ${clock}`;
  if (date === shiftDate(today, -1)) return `yesterday at ${clock}`;
  return `on ${dayText(date, yearIn(nowMs, timeZone))} at ${clock}`;
}

/** A moment still to come, in a sentence: "today at 18:55", "tomorrow at 06:32", "Mon, Oct 12 at 07:00", by the clock of the zone. */
export function whenText(ms: number, nowMs: number, timeZone: string): string {
  const date = localDate(ms, timeZone);
  const today = localDate(nowMs, timeZone);
  const clock = timeText(ms, timeZone);
  if (date === today) return `today at ${clock}`;
  if (date === shiftDate(today, 1)) return `tomorrow at ${clock}`;
  return `${dayText(date, yearIn(nowMs, timeZone))} at ${clock}`;
}

/**
 * The zone as a small chip says it: "ET" for America/New_York, "UTC", or "GMT+2" for a zone with no
 * short name of its own in English. Said once on a page, with the full name in its tooltip, instead of
 * in every card's subtitle.
 */
export function zoneChip(timeZone: string, nowMs: number): string {
  const zone = safeZone(timeZone);
  if (zone === "UTC") return "UTC";
  const name = (style: "shortGeneric" | "short") =>
    new Intl.DateTimeFormat("en-US", { timeZone: zone, timeZoneName: style }).formatToParts(new Date(nowMs)).find((part) => part.type === "timeZoneName")?.value ?? "";
  const generic = name("shortGeneric");
  return /^[A-Z]{2,5}$/.test(generic) ? generic : name("short") || zone;
}

/** "America/New_York" with its underscores taken out, which is how it reads in a sentence. */
export function zoneName(timeZone: string): string {
  return timeZone.replaceAll("_", " ");
}

/** The local hour (0-23) of an instant in a time zone. */
export function localHour(ms: number, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: safeZone(timeZone),
    hour: "2-digit",
    hourCycle: "h23",
  }).format(new Date(ms));
  return Number.parseInt(parts, 10);
}

/** "07" for hour 7: the label on a column of the hourly chart. */
export function hourLabel(hour: number): string {
  return String(hour).padStart(2, "0");
}

/** "07:00-08:00" for hour 7. */
export function hourRange(hour: number): string {
  return `${hourLabel(hour)}:00–${hourLabel((hour + 1) % 24)}:00`;
}

/** 460.4 -> "460 MB"; 1536 -> "1.5 GB". */
export function formatMegabytes(mb: number): string {
  return mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${Math.round(mb)} MB`;
}

export function formatPercent(value: number, digits = 0): string {
  return `${value.toFixed(digits)}%`;
}

/** A part of a whole as a percentage people can read: "85%", "<1%" for something that did happen but is under half a percent, "0%" for nothing. */
export function shareText(part: number, whole: number): string {
  if (whole <= 0 || part <= 0) return "0%";
  const percent = (part / whole) * 100;
  return percent < 0.5 ? "<1%" : `${Math.round(percent)}%`;
}

export function formatTemperature(celsius: number): string {
  return `${celsius.toFixed(0)} °C`;
}

/** An invalid or unknown zone name falls back to UTC, as the server does. */
function safeZone(timeZone: string): string {
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone });
    return timeZone;
  } catch {
    return "UTC";
  }
}

/** How a time-zone-aware "x ago" reads; coarse on purpose: "just now", "3 min ago", "2 h ago". */
export function formatAgo(thenMs: number, nowMs: number): string {
  const seconds = Math.max(0, Math.round((nowMs - thenMs) / 1000));
  if (seconds < 10) return "just now";
  if (seconds < 60) return `${seconds} s ago`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min ago`;
  return `${Math.round(seconds / 3600)} h ago`;
}
