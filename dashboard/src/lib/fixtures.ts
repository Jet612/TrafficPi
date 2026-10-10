/** Small profiles for tests: just enough structure, none of the editor's own logic. */
import type { Point } from "./geometry";
import type { LineConfig, Profile, StopSignConfig, ZoneConfig } from "./types";

export const rectangle = (x0: number, y0: number, x1: number, y1: number): Point[] => [
  [x0, y0],
  [x1, y0],
  [x1, y1],
  [x0, y1],
];

export function lineOf(id: string, over: Partial<LineConfig> = {}): LineConfig {
  return {
    id,
    name: "",
    a: [0.2, 0.5],
    b: [0.8, 0.5],
    classes: ["car", "truck", "bus", "motorcycle"],
    point: "ground",
    margin: null,
    persistence: null,
    ...over,
  };
}

export function zoneOf(id: string, points: Point[] = rectangle(0.2, 0.2, 0.8, 0.8), name = ""): ZoneConfig {
  return { id, name, points };
}

/** A stop sign whose approach zone is on the left of its line, which vehicles cross going right. */
export function stopOf(id: string, over: Partial<StopSignConfig> = {}): StopSignConfig {
  return {
    id,
    name: "",
    approach: rectangle(0.15, 0.45, 0.5, 0.75),
    a: [0.5, 0.75],
    b: [0.5, 0.45],
    direction: 1,
    classes: ["car", "truck", "bus", "motorcycle"],
    hold_s: 1.0,
    still_radius: 0.08,
    roll_speed: 0.7,
    min_observed_s: 0.8,
    ...over,
  };
}

export function profileOf(
  parts: {
    lines?: LineConfig[];
    detection?: ZoneConfig[];
    exclusion?: ZoneConfig[];
    stops?: StopSignConfig[];
    calibration?: Profile["calibration"];
  } = {},
): Profile {
  return {
    schema_version: 3,
    lines: parts.lines ?? [],
    detection_zones: parts.detection ?? [],
    exclusion_zones: parts.exclusion ?? [],
    stop_signs: parts.stops ?? [],
    calibration: parts.calibration ?? null,
  };
}
