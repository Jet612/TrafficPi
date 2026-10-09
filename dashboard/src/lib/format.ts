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
