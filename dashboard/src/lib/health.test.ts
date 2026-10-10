import { describe, expect, it } from "vitest";
import {
  DEFAULT_HEALTH_PERIOD,
  HEALTH_PERIODS,
  axisTicks,
  describeEpisode,
  flagNames,
  frameRateDomain,
  episodeSummary,
  hasRecord,
  healthRows,
  minutesText,
  periodHours,
  summaryTiles,
  temperatureDomain,
  trimLeadingGap,
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
    expect(healthRows(week)[0]?.label).toBe("Oct 9 14:00");
    expect(healthRows(history({ timezone: "UTC", points: [point(T0)] }))[0]?.label).toBe("18:00");
  });

  it("title a minute by its time and a longer stretch by its range, with the date again when it crosses midnight", () => {
    expect(healthRows(history())[0]?.title).toBe("Fri, Oct 9, 14:00");
    const coarse = history({ bucket_s: 600, points: [point(T0)] });
    expect(healthRows(coarse)[0]?.title).toBe("Fri, Oct 9, 14:00–14:10");
    const midnight = Date.UTC(2026, 9, 10, 3, 55) / 1000; // 23:55 in New York
    const across = history({ bucket_s: 600, points: [point(midnight)] });
    expect(healthRows(across)[0]?.title).toBe("Fri, Oct 9, 23:55–Sat, Oct 10, 00:05");
    // The year is said only when the record is not from this year.
    const old = history({ to_s: Date.UTC(2027, 0, 5) / 1000, points: [point(T0)] });
    expect(healthRows(old)[0]?.title).toBe("Fri, Oct 9, 2026, 14:00");
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
    expect(axisTicks(week, "America/New_York")).toEqual(["Oct 10 00:00", "Oct 11 00:00", "Oct 12 00:00", "Oct 13 00:00", "Oct 14 00:00", "Oct 15 00:00", "Oct 16 00:00"]);
    const month = stretches(T0 - 14 * 3600 + 3600, 180, 4 * 3600, "America/New_York"); // 30 days in four-hour stretches
    const ticks = axisTicks(month, "America/New_York");
    expect(ticks.length).toBeLessThanOrEqual(8);
    expect(ticks.length).toBeGreaterThanOrEqual(4);
    expect(ticks.every((tick) => Number(tick.split(" ")[2]?.slice(0, 2)) < 4)).toBe(true); // each is the first four-hour stretch of its day
    expect(new Set(ticks.map((tick) => tick.slice(0, 6))).size).toBe(ticks.length); // on different days
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

  it("tell a slowdown as a row: when, how long and how hot", () => {
    const row = describeEpisode(base, "America/New_York");
    expect(row).toMatchObject({ level: "warning", title: "Throttled", date: "Fri, Oct 9", start: "14:00", end: "14:07", duration: "7 min", peak: "84.2 °C", ongoing: false });
  });

  it("make a slowdown that reached the hard limit serious", () => {
    expect(describeEpisode({ ...base, peak_temp_c: 85.9 }, "UTC").level).toBe("critical");
    expect(describeEpisode({ ...base, peak_temp_c: 84.9 }, "UTC").level).toBe("warning");
  });

  it("leave out the temperature when there is none, and for what is not a slowdown", () => {
    expect(describeEpisode({ ...base, peak_temp_c: null }, "UTC").peak).toBeNull();
    expect(describeEpisode({ ...base, kind: "no-data", peak_temp_c: 70 }, "UTC").peak).toBeNull();
  });

  it("say 'so far' and have no end while it is still going on", () => {
    expect(describeEpisode({ ...base, ongoing: true }, "America/New_York")).toMatchObject({ end: null, duration: "7 min so far", ongoing: true });
  });

  it("give the day again when it ends on another day", () => {
    const night = Date.UTC(2026, 9, 10, 3, 50) / 1000; // 23:50 in New York
    const row = describeEpisode({ ...base, from_s: night, to_s: night + 20 * 60 }, "America/New_York");
    expect(row).toMatchObject({ date: "Fri, Oct 9", start: "23:50", end: "Sat, Oct 10, 00:10", duration: "20 min" });
  });

  it("tell each kind with its own level: a weak supply and a slowdown warn, a camera that is away is serious, a hole is only told", () => {
    const kinds = (["slowed", "under-voltage", "camera-offline", "no-data"] as const).map((kind) => describeEpisode({ ...base, kind }, "UTC"));
    expect(kinds.map((row) => row.level)).toEqual(["warning", "warning", "critical", "neutral"]);
    expect(kinds.map((row) => row.title)).toEqual(["Throttled", "Weak power", "Camera not working", "Nothing recorded"]);
  });

  it("have a key of their own each", () => {
    expect(describeEpisode(base, "UTC").key).not.toBe(describeEpisode({ ...base, kind: "under-voltage" }, "UTC").key);
    expect(describeEpisode(base, "UTC").key).not.toBe(describeEpisode({ ...base, from_s: T0 + 600 }, "UTC").key);
  });

  it("are summed up in one line, by kind: how many, how long, how hot", () => {
    const slow = (from: number, minutes: number, peak: number): HealthEpisode => ({ kind: "slowed", from_s: from, to_s: from + minutes * 60, ongoing: false, peak_temp_c: peak });
    expect(episodeSummary([])).toBe("");
    expect(episodeSummary([slow(T0, 7, 84.2)])).toBe("Throttled once, 7 min, up to 84.2 °C");
    expect(episodeSummary([slow(T0, 40, 83.1), slow(T0 + 3600, 60, 85.9), { ...base, kind: "camera-offline", from_s: T0 + 7200, to_s: T0 + 7200 + 480 }])).toBe(
      "Throttled 2 times, 1 h 40 min in all, up to 85.9 °C · Camera not working once, 8 min",
    );
  });
});

describe("the empty start of a record", () => {
  it("is cut off, so that a record that began this afternoon does not have a chart that is empty until then", () => {
    const rows = healthRows(
      history({ points: [point(T0, { minutes: 0, temp_c: null, cpu_percent: null, fps: null, fps_min: null, camera_up_percent: null }), point(T0 + 60), point(T0 + 120)] }),
    );
    expect(trimLeadingGap(rows).map((row) => row.t)).toEqual([T0 + 60, T0 + 120]);
  });

  it("keeps everything when the record begins at once, and a gap in the middle", () => {
    const rows = healthRows(history());
    expect(trimLeadingGap(rows)).toHaveLength(3);
  });

  it("is empty when nothing was recorded", () => {
    const rows = healthRows(history({ points: [point(T0, { minutes: 0, temp_c: null })] }));
    expect(trimLeadingGap(rows)).toHaveLength(1); // kept whole: with nothing recorded there is nothing to cut to
  });
});

describe("the numbers over the charts", () => {
  const sums = (over: Partial<SystemHistory["summary"]> = {}) => ({ minutes: 10, temp_c_max: 60, camera_up_percent: 100, slowed_minutes: 0, under_voltage_minutes: 0, ...over });
  const span = { from_s: T0, to_s: T0 + 600, episodes: [] as HealthEpisode[] };
  const slowed = (n: number): HealthEpisode[] => Array.from({ length: n }, (_, i) => ({ kind: "slowed", from_s: T0 + i * 600, to_s: T0 + i * 600 + 300, ongoing: false, peak_temp_c: 82 }));

  it("tell the hottest minute, how long it was throttled, the camera's uptime and how much was monitored", () => {
    const tiles = summaryTiles(
      { minutes: 1380, temp_c_max: 83.46, camera_up_percent: 99.74, slowed_minutes: 12, under_voltage_minutes: 1 },
      { from_s: T0, to_s: T0 + 86_400, episodes: slowed(3) },
    );
    expect(tiles.map((tile) => [tile.label, tile.value])).toEqual([
      ["Hottest", "83.5 °C"],
      ["Throttled", "12 min"],
      ["Camera uptime", "99.7%"],
      ["Monitored", "23 h 00 min"],
    ]);
    expect(tiles[0]?.flag).toEqual({ level: "warning", text: "past 80 °C: throttles" }); // 83.5 is past the soft limit
    expect(tiles[1]).toMatchObject({ note: "and 1 min of weak power" });
    expect(tiles[1]?.flag).toEqual({ level: "warning", text: "See 3 events", href: "#health-episodes" });
    expect(tiles[3]?.note).toBe("96% of the period");
  });

  it("point to the one event in the singular", () => {
    const tiles = summaryTiles(sums({ slowed_minutes: 5 }), { ...span, episodes: slowed(1) });
    expect(tiles[1]?.flag?.text).toBe("See 1 event");
  });

  it("call a reading of 80 degrees a warning and one of 85 serious, as the System page does", () => {
    const at = (temp_c_max: number) => summaryTiles(sums({ temp_c_max }), span);
    expect(at(79.9)[0]?.flag).toBeUndefined();
    expect(at(81)[0]?.flag).toMatchObject({ level: "warning" });
    expect(at(85)[0]?.flag).toEqual({ level: "critical", text: "past 85 °C: throttles hard" });
    expect(at(81)[1]).toMatchObject({ value: "never", flag: undefined });
  });

  it("say nothing where nothing was recorded", () => {
    const tiles = summaryTiles(sums({ minutes: 0, temp_c_max: null, camera_up_percent: null }), span);
    expect(tiles.map((tile) => tile.value)).toEqual(["–", "never", "–", "0 min"]);
  });

  it("never claim more than the whole period was monitored", () => {
    const tiles = summaryTiles(sums({ minutes: 9_999 }), span);
    expect(tiles[3]?.note).toBe("100% of the period");
  });

  it("know whether there is a record at all", () => {
    expect(hasRecord(history())).toBe(true);
    expect(hasRecord(history({ summary: { minutes: 0, temp_c_max: null, camera_up_percent: null, slowed_minutes: 0, under_voltage_minutes: 0 } }))).toBe(false);
  });
});
