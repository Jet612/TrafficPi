import { describe, expect, it } from "vitest";
import {
  DEFAULT_HEALTH_PERIOD,
  HEALTH_PERIODS,
  axisTicks,
  describeEpisode,
  flagNames,
  frameRateDomain,
  hasRecord,
  healthRows,
  minutesText,
  periodHours,
  summaryTiles,
  temperatureDomain,
  valueTicks,
} from "./health";
import type { HealthEpisode, HealthPoint, SystemHistory } from "./types";

const T0 = Date.UTC(2026, 9, 9, 18, 0) / 1000; // 14:00 in New York

const point = (t: number, over: Partial<HealthPoint> = {}): HealthPoint => ({
  t,
  minutes: 1,
  temp_c: 71.2,
  cpu_percent: 24.5,
  fps: 30,
  fps_min: 29.1,
  camera_up_percent: 100,
  throttled: 0,
  ...over,
});

const history = (over: Partial<SystemHistory> = {}): SystemHistory => ({
  from_s: T0,
  to_s: T0 + 180,
  bucket_s: 60,
  timezone: "America/New_York",
  points: [point(T0), point(T0 + 60, { minutes: 0, temp_c: null, cpu_percent: null, fps: null, fps_min: null, camera_up_percent: null }), point(T0 + 120)],
  episodes: [],
  summary: { minutes: 2, temp_c_max: 71.2, camera_up_percent: 100, slowed_minutes: 0, under_voltage_minutes: 0 },
  ...over,
});

describe("the periods", () => {
  it("offer an hour to a month, a day to begin with", () => {
    expect(HEALTH_PERIODS.map((one) => one.hours)).toEqual([1, 6, 24, 168, 720]);
    expect(periodHours(DEFAULT_HEALTH_PERIOD)).toBe(24);
    expect(periodHours("month")).toBe(720);
  });

  it("stay inside what the server keeps and answers", () => {
    for (const one of HEALTH_PERIODS) expect(one.hours >= 1 && one.hours <= 720).toBe(true);
  });
});

describe("the rows of the chart", () => {
  it("label a short period by the clock and a long one by the date as well, in the camera's zone", () => {
    expect(healthRows(history())[0]?.label).toBe("14:00");
    const week = history({ from_s: T0, to_s: T0 + 7 * 86_400, bucket_s: 3600, points: [point(T0)] });
    expect(healthRows(week)[0]?.label).toBe("10-09 14:00");
    expect(healthRows(history({ timezone: "UTC", points: [point(T0)] }))[0]?.label).toBe("18:00");
  });

  it("title a minute by its time and a longer stretch by its range, with the date again when it crosses midnight", () => {
    expect(healthRows(history())[0]?.title).toBe("2026-10-09 14:00");
    const coarse = history({ bucket_s: 600, points: [point(T0)] });
    expect(healthRows(coarse)[0]?.title).toBe("2026-10-09 14:00–14:10");
    const midnight = Date.UTC(2026, 9, 10, 3, 55) / 1000; // 23:55 in New York
    const across = history({ bucket_s: 600, points: [point(midnight)] });
    expect(healthRows(across)[0]?.title).toBe("2026-10-09 23:55–2026-10-10 00:05");
  });

  it("keep a stretch with nothing recorded as nothing, not as zero", () => {
    const gap = healthRows(history())[1];
    expect(gap).toMatchObject({ minutes: 0, temp: null, cpu: null, fps: null, fpsMin: null, up: null, flags: 0 });
    expect(healthRows(history())[0]).toMatchObject({ minutes: 1, temp: 71.2, cpu: 24.5, fps: 30, fpsMin: 29.1, up: 100 });
  });

  it("treat a member an older server leaves out as nothing", () => {
    const sparse = { t: T0, minutes: 1, throttled: 0 } as HealthPoint;
    expect(healthRows(history({ points: [sparse] }))[0]).toMatchObject({ temp: null, fps: null, up: null });
  });

  it("have a different id each, for the table", () => {
    const ids = healthRows(history()).map((row) => row.id);
    expect(new Set(ids).size).toBe(ids.length);
  });
});

describe("the axes", () => {
  it("make room for the hard limit of 85 degrees and for a hotter reading, in tens", () => {
    const rows = healthRows(history());
    expect(temperatureDomain(rows)).toEqual([30, 90]);
    expect(temperatureDomain(healthRows(history({ points: [point(T0, { temp_c: 88.4 })] })))).toEqual([30, 100]);
    expect(temperatureDomain(healthRows(history({ points: [point(T0, { temp_c: 87.9 })] })))).toEqual([30, 90]);
    expect(temperatureDomain([])).toEqual([30, 90]);
  });

  it("make room for the frame rate, in tens and at least to 40", () => {
    expect(frameRateDomain(healthRows(history()))).toEqual([0, 40]);
    expect(frameRateDomain(healthRows(history({ points: [point(T0, { fps: 52 })] })))).toEqual([0, 60]);
    expect(frameRateDomain(healthRows(history({ points: [point(T0, { fps: 39.5, fps_min: 12 })] })))).toEqual([0, 50]);
    expect(frameRateDomain([])).toEqual([0, 40]);
  });

  it("mark every ten", () => {
    expect(valueTicks([30, 90])).toEqual([30, 40, 50, 60, 70, 80, 90]);
    expect(valueTicks([0, 40])).toEqual([0, 10, 20, 30, 40]);
    expect(valueTicks([30, 100]).at(-1)).toBe(100);
  });
});

/** Rows of a history that begins at `fromSeconds` and goes on in stretches of `bucket` seconds. */
function stretches(fromSeconds: number, count: number, bucket: number, timezone: string) {
  const points = Array.from({ length: count }, (_, i) => point(fromSeconds + i * bucket));
  return healthRows(history({ timezone, from_s: fromSeconds, to_s: fromSeconds + count * bucket, bucket_s: bucket, points }));
}

describe("the time axis", () => {
  it("is marked every ten minutes for an hour, on the round minutes", () => {
    const rows = stretches(T0 + 5 * 60, 60, 60, "America/New_York"); // 14:05 to 15:04
    expect(axisTicks(rows, "America/New_York")).toEqual(["14:10", "14:20", "14:30", "14:40", "14:50", "15:00"]);
  });

  it("is marked every hour for six hours", () => {
    const rows = stretches(T0 + 20 * 60, 180, 120, "America/New_York"); // 14:20 to 20:19
    expect(axisTicks(rows, "America/New_York")).toEqual(["15:00", "16:00", "17:00", "18:00", "19:00", "20:00"]);
  });

  it("is marked every four hours for a day, however the day begins", () => {
    const rows = stretches(T0 - 130 * 60, 144, 600, "America/New_York"); // 11:50 to 11:49 the next day
    expect(axisTicks(rows, "America/New_York")).toEqual(["12:00", "16:00", "20:00", "00:00", "04:00", "08:00"]);
  });

  it("includes the first stretch when it falls on a round time", () => {
    const rows = stretches(T0 + 2 * 3600, 144, 600, "America/New_York"); // 16:00 to 15:50
    expect(axisTicks(rows, "America/New_York")[0]).toBe("16:00");
  });

  it("is marked at the start of each day for a week, with the date, thinned for a month", () => {
    const week = stretches(T0 - 14 * 3600 + 3600, 168, 3600, "America/New_York"); // 01:00 on the 9th, a week of hours
    expect(axisTicks(week, "America/New_York")).toEqual(["10-10 00:00", "10-11 00:00", "10-12 00:00", "10-13 00:00", "10-14 00:00", "10-15 00:00", "10-16 00:00"]);
    const month = stretches(T0 - 14 * 3600 + 3600, 180, 4 * 3600, "America/New_York"); // 30 days in four-hour stretches
    const ticks = axisTicks(month, "America/New_York");
    expect(ticks.length).toBeLessThanOrEqual(8);
    expect(ticks.length).toBeGreaterThanOrEqual(4);
    expect(ticks.every((tick) => Number(tick.slice(6, 8)) < 4)).toBe(true); // each is the first four-hour stretch of its day
    expect(new Set(ticks.map((tick) => tick.slice(0, 5))).size).toBe(ticks.length); // on different days
  });

  it("follows the camera's clock: the same instants give other labels in another zone", () => {
    const rows = stretches(T0 - 130 * 60, 144, 600, "UTC");
    expect(axisTicks(rows, "UTC")).toEqual(["16:00", "20:00", "00:00", "04:00", "08:00", "12:00"].map((hour) => hour));
  });

  it("finds the start of each day in a zone that is not a whole number of hours from UTC", () => {
    const rows = stretches(Date.UTC(2026, 9, 9, 0, 0) / 1000, 168, 3600, "Asia/Kolkata"); // a week of hours, which begin at :30 there
    const ticks = axisTicks(rows, "Asia/Kolkata");
    expect(ticks.length).toBeGreaterThanOrEqual(6);
    expect(ticks.every((tick) => tick.endsWith("00:30"))).toBe(true); // the first stretch of each local day
  });

  it("has nothing to mark with fewer than two stretches, and never repeats a label", () => {
    expect(axisTicks([], "UTC")).toEqual([]);
    expect(axisTicks(stretches(T0, 1, 60, "UTC"), "UTC")).toEqual([]);
    const ticks = axisTicks(stretches(T0, 144, 600, "UTC"), "UTC");
    expect(new Set(ticks).size).toBe(ticks.length);
  });
});

describe("what the Pi's flags say", () => {
  it("names each bit in the words the status page already knows", () => {
    expect(flagNames(0)).toEqual([]);
    expect(flagNames(0b0001)).toEqual(["under-voltage"]);
    expect(flagNames(0b1100)).toEqual(["throttled", "soft temperature limit"]);
    expect(flagNames(0b1111)).toEqual(["under-voltage", "ARM frequency capped", "throttled", "soft temperature limit"]);
  });

  it("ignore the bits that only say something happened since the boot", () => {
    expect(flagNames(0xe0000)).toEqual([]);
  });
});

describe("how long", () => {
  it("is said in minutes, hours and days", () => {
    expect(minutesText(30)).toBe("1 min"); // never "0 min"
    expect(minutesText(7 * 60)).toBe("7 min");
    expect(minutesText(65 * 60)).toBe("1 h 05 min");
    expect(minutesText(26 * 3600)).toBe("1 d 2 h");
  });
});

describe("the episodes", () => {
  const base: HealthEpisode = { kind: "slowed", from_s: T0, to_s: T0 + 7 * 60, ongoing: false, peak_temp_c: 84.24 };

  it("tell a slowdown with the temperature it reached", () => {
    const text = describeEpisode(base, "America/New_York");
    expect(text).toMatchObject({ level: "warning", title: "Slowed itself down", when: "2026-10-09 14:00 to 14:07 (7 min)" });
    expect(text.detail).toContain("Up to 84.2 °C");
  });

  it("leave out the temperature when there is none", () => {
    expect(describeEpisode({ ...base, peak_temp_c: null }, "UTC").detail).not.toContain("°C");
  });

  it("say 'until now' while it is still going on", () => {
    expect(describeEpisode({ ...base, ongoing: true }, "America/New_York").when).toBe("2026-10-09 14:00 until now (7 min so far)");
  });

  it("give the date again when it ends on another day", () => {
    const night = Date.UTC(2026, 9, 10, 3, 50) / 1000; // 23:50 in New York
    const text = describeEpisode({ ...base, from_s: night, to_s: night + 20 * 60 }, "America/New_York");
    expect(text.when).toBe("2026-10-09 23:50 to 2026-10-10 00:10 (20 min)");
  });

  it("tell each kind with its own level: a weak supply and a slowdown warn, a camera that is away is serious, a hole is only told", () => {
    const kinds = (["slowed", "under-voltage", "camera-offline", "no-data"] as const).map((kind) => describeEpisode({ ...base, kind }, "UTC"));
    expect(kinds.map((text) => text.level)).toEqual(["warning", "warning", "critical", "neutral"]);
    expect(new Set(kinds.map((text) => text.title)).size).toBe(4);
    expect(kinds[3]?.detail).toContain("not running");
  });

  it("have a key of their own each", () => {
    expect(describeEpisode(base, "UTC").key).not.toBe(describeEpisode({ ...base, kind: "under-voltage" }, "UTC").key);
    expect(describeEpisode(base, "UTC").key).not.toBe(describeEpisode({ ...base, from_s: T0 + 600 }, "UTC").key);
  });
});

describe("the numbers over the charts", () => {
  it("tell the hottest minute, how long it slowed down, the camera and how much was recorded", () => {
    const tiles = summaryTiles({ minutes: 1380, temp_c_max: 83.46, camera_up_percent: 99.74, slowed_minutes: 12, under_voltage_minutes: 1 }, { from_s: T0, to_s: T0 + 86_400 });
    expect(tiles.map((tile) => [tile.label, tile.value])).toEqual([
      ["Hottest", "83.5 °C"],
      ["Slowed down", "12 min"],
      ["Camera working", "99.7%"],
      ["Recorded", "1,380 min"],
    ]);
    expect(tiles[0]?.flag).toEqual({ level: "warning", text: "past 80 °C, where the Pi slows itself down" }); // 83.5 is past the soft limit
    expect(tiles[1]).toMatchObject({ flag: { level: "warning", text: "listed below" }, note: "and 1 min of weak power supply" });
    expect(tiles[3]?.note).toBe("a reading each minute, 96% of the period");
  });

  it("call a reading of 80 degrees a warning and one of 85 serious, as the System page does", () => {
    const at = (temp_c_max: number) =>
      summaryTiles({ minutes: 10, temp_c_max, camera_up_percent: 100, slowed_minutes: 0, under_voltage_minutes: 0 }, { from_s: T0, to_s: T0 + 600 });
    expect(at(79.9)[0]?.flag).toBeUndefined();
    expect(at(81)[0]?.flag).toMatchObject({ level: "warning" });
    expect(at(85)[0]?.flag).toMatchObject({ level: "critical" });
    expect(at(81)[1]).toMatchObject({ value: "never", flag: undefined });
  });

  it("say nothing where nothing was recorded", () => {
    const tiles = summaryTiles({ minutes: 0, temp_c_max: null, camera_up_percent: null, slowed_minutes: 0, under_voltage_minutes: 0 }, { from_s: T0, to_s: T0 + 600 });
    expect(tiles.map((tile) => tile.value)).toEqual(["–", "never", "–", "0 min"]);
  });

  it("never claim more than the whole period was recorded", () => {
    const tiles = summaryTiles({ minutes: 9_999, temp_c_max: 60, camera_up_percent: 100, slowed_minutes: 0, under_voltage_minutes: 0 }, { from_s: T0, to_s: T0 + 600 });
    expect(tiles[3]?.note).toContain("100% of the period");
  });

  it("know whether there is a record at all", () => {
    expect(hasRecord(history())).toBe(true);
    expect(hasRecord(history({ summary: { minutes: 0, temp_c_max: null, camera_up_percent: null, slowed_minutes: 0, under_voltage_minutes: 0 } }))).toBe(false);
  });
});
