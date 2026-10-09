import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  boundsOf,
  clampPoint,
  closestPointOnSegment,
  distanceToSegment,
  isSimplePolygon,
  outlineProblem,
  pointInPolygon,
  polygonArea,
  toFraction,
  toPixels,
  type Point,
} from "./geometry";

interface Case {
  name: string;
  points: Point[];
  simple: boolean;
  area: number | null;
  inside: [number, number, boolean][];
}

// The same cases the backend checks (backend/tests/test_geometry.py): both sides must agree.
const { cases } = JSON.parse(
  readFileSync(new URL("../../../backend/tests/data/polygon_cases.json", import.meta.url), "utf8"),
) as { cases: Case[] };

describe("the outline checks agree with the server's", () => {
  it.each(cases.map((c) => [c.name, c] as const))("%s", (_name, c) => {
    expect(isSimplePolygon(c.points)).toBe(c.simple);
    if (c.area !== null) expect(polygonArea(c.points)).toBeCloseTo(c.area, 12);
    for (const [x, y, expected] of c.inside) {
      expect(pointInPolygon([x, y], c.points), `[${x}, ${y}]`).toBe(expected);
    }
  });
});

describe("outlineProblem", () => {
  const square: Point[] = [[0.2, 0.2], [0.8, 0.2], [0.8, 0.8], [0.2, 0.8]];

  it("accepts a proper outline", () => {
    expect(outlineProblem(square)).toBeNull();
  });
  it("says what is wrong in the server's words", () => {
    expect(outlineProblem(square.slice(0, 2))).toMatch(/at least three/);
    expect(outlineProblem([[0.2, 0.2], [0.8, 0.8], [0.8, 0.2], [0.2, 0.8]])).toMatch(/crosses itself/);
    expect(outlineProblem([[0.5, 0.5], [0.5001, 0.5], [0.5, 0.5001]])).toMatch(/too small/);
  });
});

describe("segments and points", () => {
  it("finds the closest point on a segment, including past its ends", () => {
    expect(closestPointOnSegment([0.5, 0.9], [0.2, 0.5], [0.8, 0.5])).toEqual([0.5, 0.5]);
    expect(closestPointOnSegment([0.0, 0.9], [0.2, 0.5], [0.8, 0.5])).toEqual([0.2, 0.5]);
    expect(closestPointOnSegment([1.0, 0.1], [0.2, 0.5], [0.8, 0.5])).toEqual([0.8, 0.5]);
    expect(closestPointOnSegment([0.3, 0.3], [0.5, 0.5], [0.5, 0.5])).toEqual([0.5, 0.5]); // a point
  });
  it("measures the distance to a segment", () => {
    expect(distanceToSegment([0.5, 0.9], [0.2, 0.5], [0.8, 0.5])).toBeCloseTo(0.4);
    expect(distanceToSegment([0.9, 0.5], [0.2, 0.5], [0.8, 0.5])).toBeCloseTo(0.1);
  });
  it("converts between fractions and pixels, and clamps to the picture", () => {
    const size = { width: 640, height: 360 };
    expect(toPixels([0.5, 0.25], size)).toEqual([320, 90]);
    expect(toFraction([320, 90], size)).toEqual([0.5, 0.25]);
    expect(toFraction([-20, 400], size)).toEqual([0, 1]);
    expect(toFraction([10, 10], { width: 0, height: 0 })).toEqual([0, 0]); // before it has a size
    expect(clampPoint([1.2, -0.1])).toEqual([1, 0]);
  });
  it("gives the bounds of some points", () => {
    expect(boundsOf([[0.2, 0.9], [0.7, 0.1], [0.4, 0.5]])).toEqual({ x0: 0.2, y0: 0.1, x1: 0.7, y1: 0.9 });
  });
});
