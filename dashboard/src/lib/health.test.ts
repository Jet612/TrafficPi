import { describe, expect, it } from "vitest";
import {
  DEFAULT_HEALTH_PERIOD,
  HEALTH_PERIODS,
  axisInterval,
  describeEpisode,
  flagNames,
  frameRateDomain,
  hasRecord,
  healthRows,
  minutesText,
  periodHours,
  summaryTiles,
  temperatureDomain,
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
  it("skip labels to leave about seven", () => {
    expect(axisInterval(0)).toBe(0);
    expect(axisInterval(7)).toBe(0);
    expect(axisInterval(60)).toBe(8); // 60 minutes: a label every ninth
    expect(axisInterval(144)).toBe(20);
    expect(axisInterval(180)).toBe(25);
  });

  it("make room for the hard limit of 85 degrees and for a hotter reading", () => {
    const rows = healthRows(history());
    expect(temperatureDomain(rows)).toEqual([30, 90]);
    expect(temperatureDomain(healthRows(history({ points: [point(T0, { temp_c: 88.4 })] })))).toEqual([30, 95]);
    expect(temperatureDomain([])).toEqual([30, 90]);
  });

  it("make room for the frame rate, at least to 35", () => {
    expect(frameRateDomain(healthRows(history()))).toEqual([0, 35]);
    expect(frameRateDomain(healthRows(history({ points: [point(T0, { fps: 52 })] })))).toEqual([0, 55]);
    expect(frameRateDomain([])).toEqual([0, 35]);
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
