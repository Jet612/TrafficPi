import { atText } from "./format";
import type { SpeedEstimate } from "./types";

/**
 * What a vehicle's own "size" stands for, in metres, for a car. The stop rule measures a speed in the
 * vehicle's own sizes a second, a size being the geometric mean of the width and the height of its
 * box in the picture. That is NOT the vehicle's length: a car 4.5 m long and 1.5 m high, seen from
 * the side, has a size of sqrt(4.5 x 1.5) = 2.6 m, and seen end-on about 1.6 m. (This page first
 * took a size to be the car's length, 4.5 m, which overstated every speed about 1.8 times.) The
 * server's table for the other kinds is backend core/speed.py; an estimate made this way is good to
 * about 40%.
 */
export const SIZE_METRES = 2.5;

const MPH_PER_METRE_A_SECOND = 2.2369362920544;
const KMH_PER_METRE_A_SECOND = 3.6;

export type SpeedUnits = "mph" | "kmh";

/**
 * What a speed on a page is worth, in a sentence, for each kind of speed. The same words as the
 * server's (backend core/speed.py); tests/data/speed_cases.json holds them for both, so that they
 * cannot drift. When a calibration has been checked against vehicles of known speed (plan.md M6's
 * "done when"), MEASURED_CAVEAT is where its error band goes.
 */
export const ESTIMATE_CAVEAT =
  "A speed is an estimate: how fast a vehicle crossed the picture, compared with its own size. It is good to about 40% and is not a measurement.";
export const MEASURED_CAVEAT =
  "A speed is measured on the road as it was calibrated on the dashboard's Setup page. The calibration has not yet been checked against a vehicle of known speed, so treat the figure as unproven.";

export const unitName = (units: SpeedUnits): string => (units === "mph" ? "mph" : "km/h");

/** Metres a second as miles or kilometres an hour, unrounded. */
export function inUnits(metresPerSecond: number, units: SpeedUnits): number {
  return metresPerSecond * (units === "mph" ? MPH_PER_METRE_A_SECOND : KMH_PER_METRE_A_SECOND);
}

/**
 * A speed rounded to what an estimate can claim: a whole number below 10, then the nearest 5 (halves
 * go up). The same as backend core/speed.py `rough_speed`; both read tests/data/speed_cases.json.
 */
export function roughSpeed(value: number): number {
  if (value < 9.5) return Math.floor(value + 0.5);
  return 5 * Math.floor(value / 5 + 0.5);
}

/**
 * A speed measured on a calibrated road (plan.md M6), rounded to a whole mile or kilometre an hour
 * (halves go up). The same as backend core/speed.py `measured_speed`; both read tests/data/speed_cases.json.
 */
export const measuredSpeed = (value: number): number => Math.floor(value + 0.5);

/**
 * "about 30 mph" for an estimate, "30 mph" for a speed measured on a calibrated road, "about 50 km/h",
 * or "almost still": a speed in metres a second, in words.
 */
export function speedPhrase(metresPerSecond: number, units: SpeedUnits, measured = false): string {
  const value = inUnits(metresPerSecond, units);
  if (value < 1) return "almost still";
  return measured ? `${measuredSpeed(value)} ${unitName(units)}` : `about ${roughSpeed(value)} ${unitName(units)}`;
}

/** "30 mph", "50 km/h", or "almost still": the same speed as `speedPhrase`, short enough for a table cell. */
export function speedShort(metresPerSecond: number, units: SpeedUnits, measured = false): string {
  const value = inUnits(metresPerSecond, units);
  if (value < 1) return "almost still";
  return `${measured ? measuredSpeed(value) : roughSpeed(value)} ${unitName(units)}`;
}

const ARTICLES: Record<string, string> = { car: "a car", truck: "a truck", bus: "a bus", motorcycle: "a motorcycle", bicycle: "a bicycle" };

/** "a car", "a bus", or "a vehicle" for a kind this page has no word for. */
export const aKind = (label: string): string => ARTICLES[label] ?? "a vehicle";

/**
 * The fastest speed as a headline and a note: "about 30 mph" (an estimate) or "30 mph" (measured on a
 * calibrated road), and "a car on Fri, Oct 9 at 17:42" (how a moment reads everywhere: lib/format.ts).
 */
export function fastestText(estimate: SpeedEstimate, units: SpeedUnits, timeZone: string, nowMs: number): { speed: string; note: string } {
  return {
    speed: speedPhrase(estimate.speed_ms, units, estimate.calibrated),
    note: `${aKind(estimate.label)} ${atText(estimate.at, timeZone, nowMs)}`,
  };
}
