import { describe, expect, it } from "vitest";
import {
  CALIBRATION_ID,
  MAX_CHECKS,
  MIN_SIDE_PX,
  believedCells,
  calibrationProblems,
  checkProblems,
  checkText,
  cornersInOrder,
  defaultLengthUnit,
  lengthScale,
  lengthText,
  newCalibration,
  newCheck,
  placeText,
  probesOf,
  sidePixels,
} from "./calibration";
import { lineOf, profileOf, stopOf } from "./fixtures";
import type { CalibrationReport } from "./types";

const REPORT: CalibrationReport = {
  id: "ab12cd34",
  limit_pct: 10,
  centre_error_pct: 4.4,
  checks: [],
  probes: [],
  grid: { columns: 4, rows: 2, along_pct: [[null, 3.2, 10, 10.1], [4, 9.9, 25, 80]], across_pct: [[null, 5, 20, 30], [6, 7, 40, 90]] },
};

describe("a first rectangle", () => {
  it("is one the server would take: its corners go round it, it is big, and the sizes are in range", () => {
    const calibration = newCalibration();
    expect(calibrationProblems(calibration)).toEqual([]);
    expect(calibration.corners).toHaveLength(4);
    expect(calibration.checks).toEqual([]);
    expect(calibration.width_m).toBe(7);
    expect(calibration.length_m).toBe(30);
  });

  it("is narrower at the far end, as a road seen from above and from the side is", () => {
    const [nearLeft, nearRight, farRight, farLeft] = newCalibration().corners;
    expect(nearRight![0] - nearLeft![0]).toBeGreaterThan(farRight![0] - farLeft![0]);
    expect(farLeft![1]).toBeLessThan(nearLeft![1]); // further up the picture is further away
  });

  it("is a new object every time, so that no two profiles share its corners", () => {
    expect(newCalibration()).not.toBe(newCalibration());
    expect(newCalibration().corners).not.toBe(newCalibration().corners);
  });
});

describe("corners in order", () => {
  it("are those that go round a quadrilateral the same way at every corner, either way round", () => {
    expect(cornersInOrder([[0.3, 0.8], [0.7, 0.8], [0.6, 0.5], [0.4, 0.5]])).toBe(true);
    expect(cornersInOrder([[0.4, 0.5], [0.6, 0.5], [0.7, 0.8], [0.3, 0.8]])).toBe(true);
  });

  it("are not those of a bow tie, a dent, three on a line or fewer than three", () => {
    expect(cornersInOrder([[0.3, 0.3], [0.7, 0.7], [0.7, 0.3], [0.3, 0.7]])).toBe(false);
    expect(cornersInOrder([[0.3, 0.3], [0.7, 0.3], [0.5, 0.4], [0.5, 0.8]])).toBe(false);
    expect(cornersInOrder([[0.3, 0.3], [0.5, 0.5], [0.7, 0.7], [0.3, 0.7]])).toBe(false);
    expect(cornersInOrder([[0.3, 0.3], [0.7, 0.7]])).toBe(false);
  });
});

describe("what is wrong with a calibration", () => {
  it("says when the corners do not go round the rectangle", () => {
    const calibration = { ...newCalibration(), corners: [[0.3, 0.3], [0.7, 0.7], [0.7, 0.3], [0.3, 0.7]] as [number, number][] };
    expect(calibrationProblems(calibration)).toEqual(["the corners must go round the rectangle one after the other, without crossing"]);
  });

  it("says when two corners are a few pixels apart, and how many", () => {
    const calibration = newCalibration();
    const squashed = { ...calibration, corners: [calibration.corners[0]!, [calibration.corners[0]![0] + 0.004, calibration.corners[0]![1] + 0.002], calibration.corners[2]!, calibration.corners[3]!] as [number, number][] };
    const [problem] = calibrationProblems(squashed);
    expect(problem).toMatch(/^two of the corners are only \d+ pixels apart/);
    expect(Math.min(...sidePixels(squashed.corners))).toBeLessThan(MIN_SIDE_PX);
  });

  it("measures a side in the pixels of a 1280 by 720 picture", () => {
    const [width, length] = sidePixels([[0, 0], [0.5, 0], [0.5, 0.5], [0, 0.5]]);
    expect(width).toBe(640);
    expect(length).toBe(360);
  });

  it("says when a size is out of range or not a number", () => {
    const calibration = newCalibration();
    expect(calibrationProblems({ ...calibration, width_m: 0.1 })).toEqual(["the width must be from 0.5 to 100 m"]);
    expect(calibrationProblems({ ...calibration, length_m: 900 })).toEqual(["the length must be from 1 to 500 m"]);
    expect(calibrationProblems({ ...calibration, width_m: Number.NaN })).toHaveLength(1);
  });

  it("says what is wrong with a known distance", () => {
    const check = newCheck("c1", "", newCalibration());
    expect(checkProblems(check)).toEqual([]);
    expect(checkProblems({ ...check, b: check.a })).toEqual(["the two ends are in the same place"]);
    expect(checkProblems({ ...check, length_m: 0.1 })).toEqual(["the length must be from 0.5 to 500 m"]);
  });
});

describe("a known distance to begin with", () => {
  it("runs along the middle of the road, between a quarter and three quarters of the way", () => {
    const calibration = newCalibration();
    const check = newCheck("check-1", "Known distance 1", calibration);
    expect(check).toMatchObject({ id: "check-1", name: "Known distance 1" });
    const x = (point: [number, number]) => point[0];
    expect(x(check.a)).toBeCloseTo(0.5);
    expect(x(check.b)).toBeCloseTo(0.5);
    expect(check.a[1]).toBeGreaterThan(check.b[1]); // the near end is lower in the picture
    expect(check.length_m).toBe(15); // half the rectangle
  });

  it("is never shorter than the least the server takes", () => {
    const tiny = { ...newCalibration(), length_m: 1 };
    expect(checkProblems(newCheck("c", "", tiny))).toEqual([]);
  });
});

describe("lengths in metres or feet", () => {
  it("scale and are named", () => {
    expect(lengthScale("m")).toBe(1);
    expect(lengthScale("ft")).toBeCloseTo(3.2808, 4);
    expect(defaultLengthUnit("mph")).toBe("ft");
    expect(defaultLengthUnit("kmh")).toBe("m");
    expect(defaultLengthUnit(undefined)).toBe("m");
  });

  it.each([
    [7, "m", "7 m"],
    [12.5, "m", "12.5 m"],
    [30, "m", "30 m"],
    [30.04, "m", "30 m"],
    [29.96, "m", "30 m"],
    [7, "ft", "23 ft"],
    [2, "ft", "6.6 ft"],
    [0.5, "m", "0.5 m"],
    [100, "m", "100 m"],
  ] as const)("writes %d m in %s as %s", (metres, unit, text) => {
    expect(lengthText(metres, unit)).toBe(text);
  });
});

describe("the places the report is asked about", () => {
  it("are the middle of each counting line and of each stop line, named by what they are", () => {
    const profile = profileOf({
      lines: [lineOf("main", { a: [0.2, 0.4], b: [0.6, 0.4] })],
      stops: [stopOf("elm", { a: [0.5, 0.8], b: [0.5, 0.4] })],
    });
    expect(probesOf(profile)).toEqual([
      { id: "line:main", point: [0.4, 0.4] },
      { id: "stop:elm", point: [0.5, 0.6000000000000001] },
    ]);
  });

  it("are none when there is nothing to ask about", () => {
    expect(probesOf(profileOf())).toEqual([]);
  });
});

describe("where speeds are given", () => {
  it("is every cell where the error along the road is within the limit, the limit included", () => {
    expect(believedCells(REPORT.grid, 10)).toEqual([
      { column: 1, row: 0 },
      { column: 2, row: 0 },
      { column: 0, row: 1 },
      { column: 1, row: 1 },
    ]);
  });

  it("leaves out cells the road does not reach", () => {
    expect(believedCells({ columns: 2, rows: 1, along_pct: [[null, null]], across_pct: [[null, null]] }, 100)).toEqual([]);
  });

  it("says so when a vehicle driving across the road would be given none", () => {
    const place = { id: "line:side", metres_per_pixel_along: 0.1, metres_per_pixel_across: 0.2, error_along_pct: 6, believed: true };
    const wide = placeText({ ...REPORT, probes: [{ ...place, error_across_pct: 25 }] }, "line:side")!;
    expect(wide.believed).toBe(true);
    expect(wide.text).toMatch(/; a vehicle driving across the road is given none$/);
    const unknown = placeText({ ...REPORT, probes: [{ ...place, error_across_pct: null }] }, "line:side")!;
    expect(unknown.text).toMatch(/given none$/);
    const fine = placeText({ ...REPORT, probes: [{ ...place, error_across_pct: REPORT.limit_pct }] }, "line:side")!;
    expect(fine.text).not.toContain("across");
  });

  it("says, for a place, whether speeds are given and how well", () => {
    const report: CalibrationReport = {
      ...REPORT,
      probes: [
        { id: "stop:elm", metres_per_pixel_along: 0.12, metres_per_pixel_across: 0.06, error_along_pct: 4.6, error_across_pct: 8, believed: true },
        { id: "line:far", metres_per_pixel_along: 0.5, metres_per_pixel_across: 0.2, error_along_pct: 31.2, error_across_pct: 60, believed: false },
        { id: "line:sky", metres_per_pixel_along: null, metres_per_pixel_across: null, error_along_pct: null, error_across_pct: null, believed: false },
      ],
    };
    expect(placeText(report, "stop:elm")).toEqual({
      believed: true,
      text: "speeds are given here for a vehicle driving along the road, expected to be right to about 5%; a pixel is about 12 cm along the road",
    });
    const far = placeText(report, "line:far")!;
    expect(far.believed).toBe(false);
    expect(far.text).toContain("only good to about 31% here");
    expect(far.text).toContain("10% or better");
    expect(placeText(report, "line:sky")).toEqual({ believed: false, text: "no speed is given here: the calibration does not reach this part of the picture" });
    expect(placeText(report, "line:unknown")).toBeNull();
  });
});

describe("a known distance, measured by the map", () => {
  const check = (over: Partial<CalibrationReport["checks"][number]>): CalibrationReport => ({
    ...REPORT,
    checks: [{ id: "c1", entered_m: 21.2, measured_m: 21.2, error_pct: 0, expected_pct: 4.2, agrees: true, ...over }],
  });

  it("agrees when the map finds what was typed", () => {
    expect(checkText(check({}), "c1", "m")).toEqual({
      agrees: true,
      text: "the map finds 21.2 m, the same as the 21.2 m you typed (the map is expected to be out by about 4% for a distance like this)",
    });
  });

  it("says how far out the map is, and in which direction", () => {
    expect(checkText(check({ measured_m: 20.6, error_pct: -2.9 }), "c1", "m")!.text).toContain("2.9% shorter than the 21.2 m you typed");
    expect(checkText(check({ measured_m: 23.4, error_pct: 10.4 }), "c1", "m")!.text).toContain("10% longer than");
  });

  it("says when it does not agree, and what that means", () => {
    const said = checkText(check({ measured_m: 30, error_pct: 41.4, agrees: false }), "c1", "m")!;
    expect(said.agrees).toBe(false);
    expect(said.text).toMatch(/A corner, a length or this distance is wrong$/);
  });

  it("is told in feet when feet are wanted", () => {
    expect(checkText(check({}), "c1", "ft")!.text).toContain("69.6 ft");
  });

  it("has nothing to say about a distance with an end above the horizon", () => {
    expect(checkText(check({ measured_m: null, error_pct: null, agrees: null }), "c1", "m")!.text).toContain("above the horizon");
  });

  it("is nothing for a distance the report does not know", () => {
    expect(checkText(check({}), "other", "m")).toBeNull();
  });
});

describe("the one calibration of a profile", () => {
  it("is selected by a fixed id", () => {
    expect(CALIBRATION_ID).toBe("calibration");
    expect(MAX_CHECKS).toBe(8);
  });
});
