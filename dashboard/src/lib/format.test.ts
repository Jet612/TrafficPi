import { describe, expect, it } from "vitest";
import {
  formatAgo,
  formatClock,
  formatCount,
  formatDateTime,
  formatDateTimeSeconds,
  formatDuration,
  formatMegabytes,
  fullTimeText,
  hourLabel,
  hourRange,
  localDate,
  localHour,
  momentText,
  dayText,
  shiftDate,
  timeText,
  yearIn,
  zoneChip,
  zoneName,
} from "./format";
import { cpuSeverity, describeThrottle, diskSeverity, temperatureSeverity, temperatureWord } from "./severity";

describe("dates and times for people", () => {
  const NY = "America/New_York";
  // 2026-10-09 22:07:58 in New York (UTC-4), which is 02:07:58 UTC on the 10th.
  const evening = Date.UTC(2026, 9, 10, 2, 7, 58);

  it("writes a day as 'Fri, Oct 9', with the year only when it is not this year", () => {
    expect(dayText("2026-10-09", 2026)).toBe("Fri, Oct 9");
    expect(dayText("2025-12-31", 2026)).toBe("Wed, Dec 31, 2025");
    expect(dayText("2026-01-01", 2026)).toBe("Thu, Jan 1");
    expect(dayText("not a date", 2026)).toBe("not a date");
  });

  it("finds the local date and year in the camera's zone, not the browser's", () => {
    expect(localDate(evening, NY)).toBe("2026-10-09");
    expect(localDate(evening, "UTC")).toBe("2026-10-10");
    expect(yearIn(Date.UTC(2027, 0, 1, 2, 0, 0), NY)).toBe(2026); // still New Year's Eve there
    expect(yearIn(Date.UTC(2027, 0, 1, 6, 0, 0), NY)).toBe(2027);
  });

  it("moves a date by days, over a month's end and a leap day", () => {
    expect(shiftDate("2026-10-09", 1)).toBe("2026-10-10");
    expect(shiftDate("2026-10-31", 1)).toBe("2026-11-01");
    expect(shiftDate("2028-03-01", -1)).toBe("2028-02-29");
    expect(shiftDate("junk", 1)).toBe("junk");
  });

  it("writes a clock time in 24 hours, without seconds", () => {
    expect(timeText(evening, NY)).toBe("22:07");
    expect(timeText(Date.UTC(2026, 9, 10, 4, 5, 0), NY)).toBe("00:05"); // midnight is 00, not 24
  });

  it("writes all of an instant for a tooltip", () => {
    expect(fullTimeText(evening, NY)).toBe("Fri, Oct 9, 2026, 22:07:58");
  });

  it("says when something happened as briefly as it can be said", () => {
    const now = evening;
    expect(momentText(now - 20_000, NY, now)).toBe("just now");
    expect(momentText(now + 5_000, NY, now)).toBe("just now"); // a clock a little ahead
    expect(momentText(now - 2 * 60_000, NY, now)).toBe("2 min ago");
    expect(momentText(now - 59 * 60_000, NY, now)).toBe("59 min ago");
    expect(momentText(now - 3 * 3_600_000, NY, now)).toBe("19:07"); // earlier today
    expect(momentText(now - 23 * 3_600_000, NY, now)).toBe("Yesterday, 23:07");
    expect(momentText(now - 3 * 86_400_000, NY, now)).toBe("Tue, Oct 6, 22:07");
    expect(momentText(Date.UTC(2025, 11, 31, 20, 0, 0), NY, now)).toBe("Wed, Dec 31, 2025, 15:00");
  });

  it("names the zone as a chip and as words", () => {
    const now = evening;
    expect(zoneChip(NY, now)).toBe("ET");
    expect(zoneChip("America/Los_Angeles", now)).toBe("PT");
    expect(zoneChip("UTC", now)).toBe("UTC");
    expect(zoneChip("Not/AZone", now)).toBe("UTC"); // the server falls back to UTC, and so does this
    expect(zoneChip("Europe/Paris", now)).toMatch(/^GMT\+2$|^CEST$/); // no short name of its own in English
    expect(zoneName("America/New_York")).toBe("America/New York");
  });
});

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
    expect([60, 69.9, 70, 79.9, 80, 84.9, 85, 90].map(temperatureWord)).toEqual(["Normal", "Normal", "Warm", "Warm", "Throttling", "Throttling", "Throttling hard", "Throttling hard"]);
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
