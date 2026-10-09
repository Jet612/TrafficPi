import { describe, expect, it } from "vitest";
import { axisTicks, buildHourlyRows, countFor, niceMax, peakRow, stackKeys } from "./hourly";
import type { SeriesPoint, TodaySummary } from "./types";

const HOUR = 3_600_000;
const MIDNIGHT = Date.UTC(2026, 9, 8, 4); // local midnight in New York (UTC-4)

function today(counts: number[], startHour = 0): TodaySummary {
  return {
    date: "2026-10-08",
    timezone: "America/New_York",
    total: counts.reduce((a, b) => a + b, 0),
    by_class: {},
    lines: [],
    hourly: counts.map((count, i) => ({ t: MIDNIGHT + i * HOUR, hour: startHour + i, count })),
  };
}

function point(hourIndex: number, byClass: Record<string, number>): SeriesPoint {
  const t = MIDNIGHT + hourIndex * HOUR;
  return { t, local: "", total: Object.values(byClass).reduce((a, b) => a + b, 0), by_class: byClass, by_line: {} };
}

describe("buildHourlyRows", () => {
  it("makes a row per hour with the split by class", () => {
    const rows = buildHourlyRows(today([0, 3, 5]), [point(1, { car: 2, truck: 1 }), point(2, { car: 5 })], MIDNIGHT + 2.5 * HOUR);
    expect(rows.map((r) => [r.hour, r.total])).toEqual([[0, 0], [1, 3], [2, 5]]);
    expect(rows[1]?.byClass).toEqual({ car: 2, truck: 1 });
    expect(rows.map((r) => r.current)).toEqual([false, false, true]);
    expect(rows[2]?.label).toBe("02");
  });

  it("falls back to the day's totals while the split has not arrived", () => {
    const rows = buildHourlyRows(today([4, 0]), null, MIDNIGHT);
    expect(rows[0]).toMatchObject({ total: 4, byClass: {}, current: true });
  });

  it("trusts the split when the two answers disagree", () => {
    const rows = buildHourlyRows(today([3]), [point(0, { car: 4 })], MIDNIGHT);
    expect(rows[0]?.total).toBe(4); // a car was counted between the two requests
  });

  it("keeps a 25-hour day (clocks going back) as 25 rows", () => {
    const counts = Array.from({ length: 25 }, (_, i) => i);
    const rows = buildHourlyRows(today(counts), null, MIDNIGHT);
    expect(rows).toHaveLength(25);
  });

  it("marks no hour as current outside the day", () => {
    const rows = buildHourlyRows(today([1, 1]), null, MIDNIGHT + 10 * HOUR);
    expect(rows.some((r) => r.current)).toBe(false);
  });
});

describe("peak, stacks and axis", () => {
  const rows = buildHourlyRows(
    today([0, 2, 6, 6, 1]),
    [point(1, { car: 2 }), point(2, { car: 4, truck: 2 }), point(3, { car: 5, giraffe: 1 }), point(4, { zebra: 1 })],
    MIDNIGHT,
  );

  it("finds the busiest hour, the earlier one on a tie", () => {
    expect(peakRow(rows)?.hour).toBe(2);
    expect(peakRow(buildHourlyRows(today([0, 0]), null, MIDNIGHT))).toBeNull();
  });

  it("stacks known classes in order, with one shared other", () => {
    expect(stackKeys(rows)).toEqual(["car", "truck", "other"]);
    expect(stackKeys(buildHourlyRows(today([0]), null, MIDNIGHT))).toEqual([]);
    expect(countFor(rows[3]!, "car")).toBe(5);
    expect(countFor(rows[3]!, "other")).toBe(1);
    expect(countFor(rows[4]!, "other")).toBe(1);
    expect(countFor(rows[0]!, "truck")).toBe(0);
  });

  it("rounds the axis top up to a tidy number", () => {
    expect(niceMax(0)).toBe(4);
    expect(niceMax(3)).toBe(4);
    expect(niceMax(5)).toBe(5);
    expect(niceMax(6)).toBe(10);
    expect(niceMax(17)).toBe(20);
    expect(niceMax(22)).toBe(25);
    expect(niceMax(41)).toBe(50);
    expect(niceMax(120)).toBe(200);
    expect(niceMax(1000)).toBe(1000);
  });
});

describe("axisTicks", () => {
  it("gives whole-number gridlines with four or five intervals", () => {
    expect(axisTicks(4)).toEqual([0, 1, 2, 3, 4]);
    expect(axisTicks(5)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(axisTicks(10)).toEqual([0, 2, 4, 6, 8, 10]);
    expect(axisTicks(20)).toEqual([0, 5, 10, 15, 20]);
    expect(axisTicks(25)).toEqual([0, 5, 10, 15, 20, 25]);
    expect(axisTicks(50)).toEqual([0, 10, 20, 30, 40, 50]);
    expect(axisTicks(100)).toEqual([0, 20, 40, 60, 80, 100]);
    expect(axisTicks(200)).toEqual([0, 50, 100, 150, 200]);
    expect(axisTicks(1000)).toEqual([0, 200, 400, 600, 800, 1000]);
  });

  it("always ends on the maximum that niceMax chose", () => {
    for (const value of [1, 7, 13, 26, 99, 340, 4100, 25_000]) {
      const max = niceMax(value);
      const ticks = axisTicks(max);
      expect(ticks[0]).toBe(0);
      expect(ticks[ticks.length - 1]).toBe(max);
      expect(ticks.length).toBeLessThanOrEqual(6);
    }
  });
});
