/**
 * The speed calibration as the zone editor sees it: a rectangle on the road with
 * its width and length, and known distances that test it. What lives here is what can be said
 * without asking the server: a first rectangle, the checks the server also makes (so that "Save"
 * does not fail for no visible reason), lengths in metres or feet, and which cells of the picture
 * speeds are given in. What the map itself finds (metres to a pixel, how far it can be believed)
 * stays on the server, which the editor asks (`api.calibrationReport`), so that the maths has one
 * home.
 */
import type { Point } from "./geometry";
import type {
  CalibrationConfig,
  CalibrationGrid,
  CalibrationProbe,
  CalibrationReport,
  CheckConfig,
  Profile,
} from "./types";

/** The calibration is one element of a profile; this is the id the editor selects it by. */
export const CALIBRATION_ID = "calibration";
export const CALIBRATION_NAME = "Speed calibration";

/** The server takes this many known distances, and these sizes (backend core/ground.py and core/profile.py). */
export const MAX_CHECKS = 8;
export const MIN_WIDTH_M = 0.5;
export const MAX_WIDTH_M = 100;
export const MIN_LENGTH_M = 1;
export const MAX_LENGTH_M = 500;
export const MIN_CHECK_M = 0.5;

/** A road of two lanes and a stretch to match, which the owner then makes right. */
export const DEFAULT_WIDTH_M = 7;
export const DEFAULT_LENGTH_M = 30;

/**
 * Two corners closer than this (a fraction of the picture's width) are a slip of the mouse. The
 * server's limit is 25 pixels of a picture 1280 pixels wide, and 16:9 pictures (the camera's) are
 * the only ones it knows about.
 */
export const MIN_SIDE_PX = 25;
const PICTURE_WIDTH_PX = 1280;
const PICTURE_HEIGHT_PX = 720;

/**
 * A first rectangle: the near edge across the foot of the picture, the far edge higher up and
 * shorter, as a road seen from above and from the side is. The corners go round it, starting at
 * the near left: the first side is the width, the second the length.
 */
export function newCalibration(): CalibrationConfig {
  return {
    corners: [
      [0.3, 0.85],
      [0.7, 0.85],
      [0.6, 0.5],
      [0.4, 0.5],
    ],
    width_m: DEFAULT_WIDTH_M,
    length_m: DEFAULT_LENGTH_M,
    checks: [],
  };
}

/** A check starts as a short run along the middle of the road, for the owner to put on a known distance. */
export function newCheck(id: string, name: string, calibration: CalibrationConfig): CheckConfig {
  const [a, b, c, d] = calibration.corners as [Point, Point, Point, Point];
  const near: Point = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  const far: Point = [(c[0] + d[0]) / 2, (c[1] + d[1]) / 2];
  const along = (t: number): Point => [near[0] + (far[0] - near[0]) * t, near[1] + (far[1] - near[1]) * t];
  return { id, name, a: along(0.25), b: along(0.75), length_m: Math.round(calibration.length_m / 2) || MIN_CHECK_M };
}

/** The side lengths of the quadrilateral in pixels of a 1280 by 720 picture: width, length, and the two sides back. */
export function sidePixels(corners: readonly Point[]): number[] {
  return corners.map((p, i) => {
    const q = corners[(i + 1) % corners.length]!;
    return Math.hypot((q[0] - p[0]) * PICTURE_WIDTH_PX, (q[1] - p[1]) * PICTURE_HEIGHT_PX);
  });
}

/**
 * Whether the corners go round a quadrilateral one after the other: it turns the same way at every
 * corner, and is not flat. The same test as the server's (core/ground.py `_is_convex`).
 */
export function cornersInOrder(corners: readonly Point[]): boolean {
  if (corners.length < 3) return false;
  const turns = corners.map((p1, i) => {
    const p0 = corners[(i + corners.length - 1) % corners.length]!;
    const p2 = corners[(i + 1) % corners.length]!;
    return (p1[0] - p0[0]) * (p2[1] - p1[1]) - (p1[1] - p0[1]) * (p2[0] - p1[0]);
  });
  if (turns.some((turn) => Math.abs(turn) < 1e-12)) return false;
  return turns.every((turn) => turn > 0) || turns.every((turn) => turn < 0);
}

/**
 * What is wrong with a calibration, or its checks, in words, without the name of the element: the
 * errors the server would refuse a save for. (How well the map will do is a different question,
 * and the server's report answers it.)
 */
export function calibrationProblems(calibration: CalibrationConfig): string[] {
  const found: string[] = [];
  if (!cornersInOrder(calibration.corners)) {
    found.push("the corners must go round the rectangle one after the other, without crossing");
  } else {
    const small = Math.min(...sidePixels(calibration.corners));
    if (small < MIN_SIDE_PX) {
      found.push(`two of the corners are only ${Math.round(small)} pixels apart; the rectangle is too small in the picture to calibrate anything`);
    }
  }
  if (!Number.isFinite(calibration.width_m) || calibration.width_m < MIN_WIDTH_M || calibration.width_m > MAX_WIDTH_M) {
    found.push(`the width must be from ${MIN_WIDTH_M} to ${MAX_WIDTH_M} m`);
  }
  if (!Number.isFinite(calibration.length_m) || calibration.length_m < MIN_LENGTH_M || calibration.length_m > MAX_LENGTH_M) {
    found.push(`the length must be from ${MIN_LENGTH_M} to ${MAX_LENGTH_M} m`);
  }
  return found;
}

/** What is wrong with one known distance. */
export function checkProblems(check: CheckConfig): string[] {
  const found: string[] = [];
  if (check.a[0] === check.b[0] && check.a[1] === check.b[1]) found.push("the two ends are in the same place");
  if (!Number.isFinite(check.length_m) || check.length_m < MIN_CHECK_M || check.length_m > MAX_LENGTH_M) {
    found.push(`the length must be from ${MIN_CHECK_M} to ${MAX_LENGTH_M} m`);
  }
  return found;
}

// --- lengths in metres or feet --------------------------------------------------------------------

export type LengthUnit = "m" | "ft";

export const FEET_PER_METRE = 3.280839895;

/** Multiply a length in metres by this to show it in the unit. */
export const lengthScale = (unit: LengthUnit): number => (unit === "ft" ? FEET_PER_METRE : 1);

export const lengthUnitName = (unit: LengthUnit): string => (unit === "ft" ? "feet" : "metres");

/** The unit a person measures a road in, going by the units they read speeds in. */
export const defaultLengthUnit = (speedUnits: "mph" | "kmh" | undefined): LengthUnit => (speedUnits === "mph" ? "ft" : "m");

/** "7 m", "12.5 m", "23 ft": a length for a label, short and without false precision. */
export function lengthText(metres: number, unit: LengthUnit): string {
  const value = metres * lengthScale(unit);
  const digits = value >= 100 ? 0 : 1; // a tenth is a hand's breadth, and the zero after it is not worth saying
  const text = Number(value.toFixed(digits)).toString();
  return `${text} ${unit}`;
}

// --- where speeds are given (the server's report) -----------------------------------------------------

export const lineProbeId = (id: string): string => `line:${id}`;
export const stopProbeId = (id: string): string => `stop:${id}`;

/** The places the report is asked about: the middle of each counting line and of each stop line. */
export function probesOf(profile: Profile): CalibrationProbe[] {
  const middle = (a: Point, b: Point): [number, number] => [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  return [
    ...profile.lines.map((line) => ({ id: lineProbeId(line.id), point: middle(line.a, line.b) })),
    ...profile.stop_signs.map((sign) => ({ id: stopProbeId(sign.id), point: middle(sign.a, sign.b) })),
  ];
}

export interface Cell {
  column: number;
  row: number;
}

/** The cells of the report's grid where a vehicle driving along the road is given a speed. */
export function believedCells(grid: CalibrationGrid, limitPct: number): Cell[] {
  const found: Cell[] = [];
  grid.along_pct.forEach((cells, row) => {
    cells.forEach((error, column) => {
      if (error !== null && error <= limitPct) found.push({ column, row });
    });
  });
  return found;
}

/** What the report says about a place: whether speeds are given there, and why or why not. */
export function placeText(report: CalibrationReport, id: string): { believed: boolean; text: string } | null {
  const place = report.probes.find((probe) => probe.id === id);
  if (place === undefined) return null;
  if (place.believed) {
    const error = place.error_along_pct === null ? "" : `, expected to be right to about ${Math.round(place.error_along_pct)}%`;
    const pixel = place.metres_per_pixel_along === null ? "" : `; a pixel is about ${Math.round(place.metres_per_pixel_along * 100)} cm along the road`;
    const across = place.error_across_pct === null || place.error_across_pct > report.limit_pct ? "; a vehicle driving across the road is given none" : "";
    return { believed: true, text: `speeds are given here for a vehicle driving along the road${error}${pixel}${across}` };
  }
  return {
    believed: false,
    text:
      place.error_along_pct === null
        ? "no speed is given here: the calibration does not reach this part of the picture"
        : `no speed is given here: the calibration is only good to about ${Math.round(place.error_along_pct)}% here, and speeds are given where it is ${report.limit_pct}% or better. A larger rectangle, reaching this place, would help`,
  };
}

/** What the map found for a known distance, in a sentence, and whether it agrees with what was typed. */
export function checkText(report: CalibrationReport, id: string, unit: LengthUnit): { agrees: boolean | null; text: string } | null {
  const result = report.checks.find((check) => check.id === id);
  if (result === undefined) return null;
  if (result.measured_m === null || result.error_pct === null) {
    return { agrees: null, text: "an end of this distance is above the horizon, so the map has nothing to say about it" };
  }
  const found = lengthText(result.measured_m, unit);
  const typed = lengthText(result.entered_m, unit);
  const size = Math.abs(result.error_pct);
  const comparison =
    size < 0.5 ? "the same as" : `${size.toFixed(size < 10 ? 1 : 0)}% ${result.error_pct < 0 ? "shorter" : "longer"} than`;
  const expected = result.expected_pct === null ? "" : ` (the map is expected to be out by about ${Math.round(result.expected_pct)}% for a distance like this)`;
  const sentence = `the map finds ${found}, ${comparison} the ${typed} you typed${expected}`;
  return { agrees: result.agrees, text: result.agrees === false ? `${sentence}. A corner, a length or this distance is wrong` : sentence };
}
