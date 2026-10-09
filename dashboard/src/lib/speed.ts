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

/** "about 30 mph", "about 50 km/h", or "almost still": a speed in metres a second, in words. */
export function speedPhrase(metresPerSecond: number, units: SpeedUnits): string {
  const value = inUnits(metresPerSecond, units);
  return value < 1 ? "almost still" : `about ${roughSpeed(value)} ${unitName(units)}`;
}

/** "30 mph", "50 km/h", or "almost still": the same speed as `speedPhrase`, short enough for a table cell. */
export function speedShort(metresPerSecond: number, units: SpeedUnits): string {
  const value = inUnits(metresPerSecond, units);
  return value < 1 ? "almost still" : `${roughSpeed(value)} ${unitName(units)}`;
}

const ARTICLES: Record<string, string> = { car: "a car", truck: "a truck", bus: "a bus", motorcycle: "a motorcycle", bicycle: "a bicycle" };

/** "a car", "a bus", or "a vehicle" for a kind this page has no word for. */
export const aKind = (label: string): string => ARTICLES[label] ?? "a vehicle";

/** "Fri 9 Oct, 17:42": when something happened, on the camera's clock. */
export function passedText(ms: number, timeZone: string): string {
  let zone = timeZone;
  try {
    new Intl.DateTimeFormat("en-GB", { timeZone });
  } catch {
    zone = "UTC";
  }
  const parts = Object.fromEntries(
    new Intl.DateTimeFormat("en-GB", { timeZone: zone, weekday: "short", day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" })
      .formatToParts(new Date(ms))
      .map((part) => [part.type, part.value]),
  );
  return `${parts.weekday} ${parts.day} ${parts.month}, ${parts.hour}:${parts.minute}`;
}

/** The fastest estimate as a headline and a note: "about 30 mph" and "a car, Fri 9 Oct, 17:42". */
export function fastestText(estimate: SpeedEstimate, units: SpeedUnits, timeZone: string): { speed: string; note: string } {
  return { speed: speedPhrase(estimate.speed_ms, units), note: `${aKind(estimate.label)}, ${passedText(estimate.at, timeZone)}` };
}
