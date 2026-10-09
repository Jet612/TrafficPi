import type { components } from "./api-types";

type Schemas = components["schemas"];

// Shapes of the REST API, generated from its OpenAPI schema (scripts/gen_api_types.sh).
export type AuthState = Schemas["AuthState"];
export type StatusResponse = Schemas["StatusResponse"];
export type TodaySummary = Schemas["TodaySummary"];
export type LineTotals = Schemas["LineTotals"];
export type SeriesPoint = Schemas["SeriesPoint"];
export type EventOut = Schemas["EventOut"];
export type ProfileOut = Schemas["ProfileOut"];
export type ProfileSaved = Schemas["ProfileSaved"];
export type ProfileCheck = Schemas["ProfileCheck"];
export type ReferenceSaved = Schemas["ReferenceSaved"];
export type Profile = Schemas["Profile"];
export type LineConfig = Schemas["CountingLineConfig"];
export type ZoneConfig = Schemas["ZoneConfig"];
export type SystemMetrics = Schemas["SystemMetrics"];
export type VisionStatus = Schemas["VisionStatus"];

/**
 * The live socket is not part of OpenAPI, so its messages are described here. They come from
 * `LiveState.snapshot()` in backend/trafficpi/vision/service.py, relayed by the API; the API also
 * sends {"type": "offline"} while it cannot reach the vision service. Boxes and points are
 * fractions (0-1) of the frame, with y pointing down.
 */
export interface LiveTrack {
  id: number;
  label: string;
  score: number;
  box: [number, number, number, number]; // x0, y0, x1, y1
  velocity: [number, number];
  confirmed: boolean;
}

export interface LiveLine {
  id: string;
  name: string;
  a: [number, number];
  b: [number, number];
}

/** An area of the picture the pipeline looks at (detection) or ignores (exclusion). */
export interface LiveZone {
  id: string;
  name: string;
  kind: "detection" | "exclusion";
  points: [number, number][];
}

export interface LiveEvent {
  t: number; // ms since the epoch
  line: string;
  direction: number; // +1 or -1
  class: string;
  track: number;
}

export interface LiveState {
  type: "state";
  uptime_s: number;
  updated_ms: number;
  frame: number;
  fps: number;
  camera: { ok: boolean; error: string | null };
  detector: string;
  profile_version: number;
  lines: LiveLine[];
  zones: LiveZone[];
  tracks: LiveTrack[];
  events: LiveEvent[];
  system: SystemMetrics;
}

export type LiveMessage = LiveState | { type: "offline" };

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isNumber = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);

function pair(value: unknown): [number, number] | null {
  return Array.isArray(value) && value.length === 2 && value.every(isNumber)
    ? [value[0] as number, value[1] as number]
    : null;
}

function quad(value: unknown): [number, number, number, number] | null {
  return Array.isArray(value) && value.length === 4 && value.every(isNumber)
    ? [value[0] as number, value[1] as number, value[2] as number, value[3] as number]
    : null;
}

function parseTrack(value: unknown): LiveTrack | null {
  if (!isRecord(value) || !isNumber(value.id) || typeof value.label !== "string") return null;
  const box = quad(value.box);
  if (!box) return null;
  return {
    id: value.id,
    label: value.label,
    score: isNumber(value.score) ? value.score : 0,
    box,
    velocity: pair(value.velocity) ?? [0, 0],
    confirmed: value.confirmed === true,
  };
}

function parseLine(value: unknown): LiveLine | null {
  if (!isRecord(value) || typeof value.id !== "string") return null;
  const a = pair(value.a);
  const b = pair(value.b);
  if (!a || !b) return null;
  return { id: value.id, name: typeof value.name === "string" ? value.name : "", a, b };
}

/** A zone with a corner that is not a pair of numbers is dropped whole: half an outline would mislead. */
function parseZone(value: unknown): LiveZone | null {
  if (!isRecord(value) || typeof value.id !== "string") return null;
  if (value.kind !== "detection" && value.kind !== "exclusion") return null;
  if (!Array.isArray(value.points) || value.points.length < 3) return null;
  const points: [number, number][] = [];
  for (const raw of value.points) {
    const point = pair(raw);
    if (!point) return null;
    points.push(point);
  }
  return { id: value.id, name: typeof value.name === "string" ? value.name : "", kind: value.kind, points };
}

function parseEvent(value: unknown): LiveEvent | null {
  if (!isRecord(value) || !isNumber(value.t) || typeof value.line !== "string") return null;
  return {
    t: value.t,
    line: value.line,
    direction: value.direction === -1 ? -1 : 1,
    class: typeof value.class === "string" ? value.class : "",
    track: isNumber(value.track) ? value.track : -1,
  };
}

function list<T>(value: unknown, parse: (item: unknown) => T | null): T[] {
  if (!Array.isArray(value)) return [];
  return value.map(parse).filter((item): item is T => item !== null);
}

/** Read one socket message. Malformed input gives null, and bad entries in a list are dropped. */
export function parseLiveMessage(text: string): LiveMessage | null {
  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    return null;
  }
  if (!isRecord(data)) return null;
  if (data.type === "offline") return { type: "offline" };
  if (data.type !== "state") return null;
  const camera = isRecord(data.camera) ? data.camera : {};
  const system = isRecord(data.system) ? data.system : {};
  return {
    type: "state",
    uptime_s: isNumber(data.uptime_s) ? data.uptime_s : 0,
    updated_ms: isNumber(data.updated_ms) ? data.updated_ms : 0,
    frame: isNumber(data.frame) ? data.frame : -1,
    fps: isNumber(data.fps) ? data.fps : 0,
    camera: {
      ok: camera.ok === true,
      error: typeof camera.error === "string" ? camera.error : null,
    },
    detector: typeof data.detector === "string" ? data.detector : "",
    profile_version: isNumber(data.profile_version) ? data.profile_version : 0,
    lines: list(data.lines, parseLine),
    zones: list(data.zones, parseZone),
    tracks: list(data.tracks, parseTrack),
    events: list(data.events, parseEvent),
    system: {
      cpu_cores: isNumber(system.cpu_cores) ? system.cpu_cores : null,
      cpu_percent: isNumber(system.cpu_percent) ? system.cpu_percent : null,
      rss_mb: isNumber(system.rss_mb) ? system.rss_mb : null,
      soc_temp_c: isNumber(system.soc_temp_c) ? system.soc_temp_c : null,
      throttled_now: strings(system.throttled_now),
      throttled_since_boot: strings(system.throttled_since_boot),
    },
  };
}

function strings(value: unknown): string[] {
  return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
}
