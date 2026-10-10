import { describe, expect, it } from "vitest";
import { axisTicks, axisWidth, niceMax } from "./hourly";

describe("niceMax", () => {
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

describe("the width of an axis", () => {
  it("grows with its widest label, so that 1,000 is not cut to ,000", () => {
    expect(axisWidth(25)).toBe(36);
    expect(axisWidth(500)).toBe(37); // "500": three characters, a little over the least
    expect(axisWidth(1000)).toBe(52); // "1,000"
    expect(axisWidth(25_000)).toBe(59); // "25,000"
    expect(axisWidth(1_000_000)).toBe(82); // "1,000,000"
  });

  it("makes room for 100% on a share axis, whatever the counts", () => {
    expect(axisWidth(4, true)).toBe(44);
    expect(axisWidth(1_000_000, true)).toBe(44);
  });
});
