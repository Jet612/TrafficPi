/** Small profiles for tests: just enough structure, none of the editor's own logic. */
import type { Point } from "./geometry";
import type { LineConfig, Profile, ZoneConfig } from "./types";

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

export function profileOf(
  parts: { lines?: LineConfig[]; detection?: ZoneConfig[]; exclusion?: ZoneConfig[] } = {},
): Profile {
  return {
    schema_version: 1,
    lines: parts.lines ?? [],
    detection_zones: parts.detection ?? [],
    exclusion_zones: parts.exclusion ?? [],
  };
}
