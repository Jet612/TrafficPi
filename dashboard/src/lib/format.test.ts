import { describe, expect, it } from "vitest";
import {
  formatAgo,
  formatClock,
  formatCount,
  formatDateTime,
  formatDateTimeSeconds,
  formatDuration,
  formatMegabytes,
  hourLabel,
  hourRange,
  localHour,
} from "./format";
import { cpuSeverity, describeThrottle, diskSeverity, temperatureSeverity } from "./severity";

describe("formatting", () => {
  it("groups thousands", () => {
    expect(formatCount(0)).toBe("0");
    expect(formatCount(1234567)).toBe("1,234,567");
  });

  it("says durations at the right grain", () => {
    expect(formatDuration(0)).toBe("0 s");
    expect(formatDuration(45.9)).toBe("45 s");
    expect(formatDuration(312)).toBe("5 m 12 s");
    expect(formatDuration(7500)).toBe("2 h 05 m");
    expect(formatDuration(3 * 86400 + 4 * 3600 + 59)).toBe("3 d 4 h");
    expect(formatDuration(-5)).toBe("0 s");
  });

  it("shows an event's moment down to the second, in the zone of the camera", () => {
    const instant = Date.UTC(2026, 9, 9, 4, 16, 56);
    expect(formatDateTimeSeconds(instant, "UTC")).toBe("2026-10-09 04:16:56");
    expect(formatDateTimeSeconds(instant, "America/New_York")).toBe("2026-10-09 00:16:56");
    expect(formatDateTimeSeconds(Date.UTC(2026, 9, 9, 4, 17, 0), "UTC")).toBe("2026-10-09 04:17:00"); // a minute, not a minute less a second
    expect(formatDateTimeSeconds(instant, "Mars/Olympus")).toBe("2026-10-09 04:16:56"); // a zone that is not one: UTC
  });

  it("shows the date and time in the configured zone, across a day boundary and a daylight-saving change", () => {
    const instant = Date.UTC(2026, 9, 9, 4, 16, 56); // 04:16 UTC on 9 October
    expect(formatDateTime(instant, "UTC")).toBe("2026-10-09 04:16");
    expect(formatDateTime(instant, "America/New_York")).toBe("2026-10-09 00:16"); // UTC-4 in summer time
    expect(formatDateTime(instant, "Asia/Tokyo")).toBe("2026-10-09 13:16");
    // Midnight is 00:00, not 24:00; and the clocks went back on 1 November 2026 (06:00 UTC).
    expect(formatDateTime(Date.UTC(2026, 9, 9, 4, 0, 0), "America/New_York")).toBe("2026-10-09 00:00");
    expect(formatDateTime(Date.UTC(2026, 10, 1, 5, 30, 0), "America/New_York")).toBe("2026-11-01 01:30");
    expect(formatDateTime(Date.UTC(2026, 10, 1, 6, 30, 0), "America/New_York")).toBe("2026-11-01 01:30");
    expect(formatDateTime(instant, "Not/AZone")).toBe("2026-10-09 04:16"); // falls back to UTC, as the server does
  });

  it("shows the clock in the configured zone, not the browser's", () => {
    const noonUtc = Date.UTC(2026, 9, 8, 12, 0, 9); // 2026-10-08 12:00:09 UTC
    expect(formatClock(noonUtc, "UTC")).toBe("12:00:09");
    expect(formatClock(noonUtc, "America/New_York")).toBe("08:00:09"); // daylight time, UTC-4
    expect(formatClock(noonUtc, "Asia/Kolkata")).toBe("17:30:09");
    expect(formatClock(noonUtc, "Not/AZone")).toBe("12:00:09"); // falls back to UTC
  });

  it("finds the local hour, also at midnight", () => {
    expect(localHour(Date.UTC(2026, 9, 8, 4, 30), "America/New_York")).toBe(0);
    expect(localHour(Date.UTC(2026, 9, 8, 3, 59), "America/New_York")).toBe(23);
  });

  it("labels hours", () => {
    expect(hourLabel(7)).toBe("07");
    expect(hourRange(7)).toBe("07:00–08:00");
    expect(hourRange(23)).toBe("23:00–00:00");
  });

  it("formats memory and relative times", () => {
    expect(formatMegabytes(460.4)).toBe("460 MB");
    expect(formatMegabytes(1536)).toBe("1.5 GB");
    expect(formatAgo(1000, 1500)).toBe("just now");
    expect(formatAgo(0, 42_000)).toBe("42 s ago");
    expect(formatAgo(0, 5 * 60_000)).toBe("5 min ago");
    expect(formatAgo(0, 3 * 3600_000)).toBe("3 h ago");
    expect(formatAgo(5000, 1000)).toBe("just now"); // a clock a little ahead is not "in the future"
  });
});

describe("severity", () => {
  it("flags a hot SoC at the Pi's own limits", () => {
    expect(temperatureSeverity(79.9)).toBe("ok");
    expect(temperatureSeverity(80)).toBe("warning");
    expect(temperatureSeverity(85)).toBe("critical");
  });

  it("flags a filling disk and a busy CPU", () => {
    expect(diskSeverity(50)).toBe("ok");
    expect(diskSeverity(90)).toBe("warning");
    expect(diskSeverity(97)).toBe("critical");
    expect(cpuSeverity(60)).toBe("ok");
    expect(cpuSeverity(90)).toBe("warning");
    expect(cpuSeverity(99)).toBe("critical");
  });

  it("explains throttle flags and leaves unknown ones alone", () => {
    expect(describeThrottle("under-voltage")).toContain("power supply");
    expect(describeThrottle("ARM frequency capped")).toContain("capped");
    expect(describeThrottle("soft temperature limit occurred")).toBe("it ran hot");
    expect(describeThrottle("something new")).toBe("something new");
  });
});
