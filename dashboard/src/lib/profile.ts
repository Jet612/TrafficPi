/**
 * The camera profile as the zone editor sees it: the lines and zones in one document, plus what can
 * be said about them without asking the server. The shape comes from the API (generated types);
 * the server stays the judge of what can be saved, and `problems` only gives early, readable notice.
 */
import { CALIBRATION_ID, CALIBRATION_NAME, MAX_CHECKS, calibrationProblems, checkProblems } from "./calibration";
import { CLASS_ORDER } from "./classes";
import { MIN_ZONE_AREA, centreOf, distanceToPolygon, outlineProblem, pointInPolygon, polygonArea, sideOfLine, type Point } from "./geometry";
import type { CalibrationConfig, CheckConfig, LineConfig, Profile, StopSignConfig, ZoneConfig } from "./types";

export type ElementKind = "line" | "detection" | "exclusion" | "stop" | "calibration" | "check";

/**
 * The speed calibration as an element of the editor: its configuration, with the id and the name the
 * editor selects and lists it by (a profile has at most one, and it has neither itself).
 */
export type CalibrationElement = CalibrationConfig & { id: string; name: string };

export interface ElementRef {
  kind: ElementKind;
  id: string;
}

export type Element =
  | { kind: "line"; value: LineConfig }
  | { kind: "detection" | "exclusion"; value: ZoneConfig }
  | { kind: "stop"; value: StopSignConfig }
  | { kind: "calibration"; value: CalibrationElement }
  | { kind: "check"; value: CheckConfig };

export const SCHEMA_VERSION = 3;
/** The server accepts this many lines, and this many zones of each kind. */
export const MAX_ELEMENTS = 32;
export const MAX_POINTS = 32;
export const MAX_NAME_LENGTH = 80;
/** A counting line shorter than this (a fraction of the picture) is a slip, not a line. */
export const MIN_LINE_LENGTH = 0.01;

/** The kinds a new counting line counts. Bicycles and people are there to be ticked. */
export const DEFAULT_LINE_CLASSES = ["car", "truck", "bus", "motorcycle"];
/** The kinds of object a line can be told to count: the ones the detector reports. */
export const COUNTABLE_CLASSES: readonly string[] = CLASS_ORDER;

/** The kinds a new stop sign makes stop: the same four as a counting line counts. */
export const DEFAULT_STOP_CLASSES = DEFAULT_LINE_CLASSES;
/** The kinds that can be told to stop. People are not among them: they cross the road. */
export const STOPPABLE_CLASSES: readonly string[] = ["car", "truck", "bus", "motorcycle", "bicycle"];

/** What counts as a stop to begin with: the server fills in the same when they are left out. */
export const STOP_DEFAULTS = { hold_s: 1.0, still_radius: 0.08, roll_speed: 0.7, min_observed_s: 0.8 } as const;
export type StopNumber = keyof typeof STOP_DEFAULTS;
/** The range the server takes for each number (what the form allows), and what each is called. */
export const STOP_NUMBERS: Record<StopNumber, { min: number; max: number; label: string }> = {
  hold_s: { min: 0.2, max: 10, label: "how long it must stand still" },
  still_radius: { min: 0.01, max: 0.5, label: "how little it may move meanwhile" },
  roll_speed: { min: 0.05, max: 10, label: "the speed below which it is rolling" },
  min_observed_s: { min: 0.1, max: 10, label: "how long it must be watched" },
};

export const KIND_LABEL: Record<ElementKind, string> = {
  line: "Counting line",
  detection: "Detection zone",
  exclusion: "Exclusion zone",
  stop: "Stop sign",
  calibration: "Speed calibration",
  check: "Known distance",
};

export function emptyProfile(): Profile {
  return { schema_version: SCHEMA_VERSION, lines: [], detection_zones: [], exclusion_zones: [], stop_signs: [], calibration: null };
}

export const sameRef = (a: ElementRef | null | undefined, b: ElementRef | null | undefined): boolean =>
  a === b || (!!a && !!b && a.kind === b.kind && a.id === b.id);

/**
 * Every element, zones first (they sit underneath), then the speed calibration, the stop signs, the
 * lines, and the known distances that test the calibration. This is also the drawing order.
 */
export function elements(profile: Profile): Element[] {
  const { calibration } = profile;
  return [
    ...profile.detection_zones.map((value): Element => ({ kind: "detection", value })),
    ...profile.exclusion_zones.map((value): Element => ({ kind: "exclusion", value })),
    ...(calibration ? [{ kind: "calibration", value: { ...calibration, id: CALIBRATION_ID, name: CALIBRATION_NAME } } as Element] : []),
    ...profile.stop_signs.map((value): Element => ({ kind: "stop", value })),
    ...profile.lines.map((value): Element => ({ kind: "line", value })),
    ...(calibration?.checks ?? []).map((value): Element => ({ kind: "check", value })),
  ];
}

export function findElement(profile: Profile, ref: ElementRef): Element | undefined {
  return elements(profile).find((element) => sameRef({ kind: element.kind, id: element.value.id }, ref));
}

export const refOf = (element: Element): ElementRef => ({ kind: element.kind, id: element.value.id });

/** The name to show for an element: its own, or its kind and a number when it has none. */
export function displayName(profile: Profile, ref: ElementRef): string {
  const element = findElement(profile, ref);
  if (element?.value.name) return element.value.name;
  const same = elements(profile).filter((other) => other.kind === ref.kind);
  const position = same.findIndex((other) => other.value.id === ref.id);
  return `${KIND_LABEL[ref.kind]} ${position === -1 ? "" : position + 1}`.trim();
}

/** The next free "Counting line 3": numbered after the highest in use, so a deleted one is not reused. */
export function suggestName(profile: Profile, kind: ElementKind): string {
  const prefix = `${KIND_LABEL[kind]} `;
  let highest = 0;
  for (const element of elements(profile)) {
    if (element.kind !== kind) continue;
    const match = new RegExp(`^${prefix}(\\d+)$`).exec(element.value.name);
    if (match) highest = Math.max(highest, Number(match[1]));
  }
  const count = elements(profile).filter((element) => element.kind === kind).length;
  return `${prefix}${Math.max(highest, count) + 1}`;
}

const ID_PREFIX: Record<ElementKind, string> = { line: "line", detection: "area", exclusion: "ignore", stop: "stop", calibration: "calibration", check: "check" };

function randomHex(length: number): string {
  const bytes = new Uint8Array(Math.ceil(length / 2));
  // getRandomValues works on plain http (a page on the LAN); randomUUID would not.
  if (typeof crypto !== "undefined" && "getRandomValues" in crypto) crypto.getRandomValues(bytes);
  else bytes.forEach((_, i) => (bytes[i] = Math.floor(Math.random() * 256)));
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("").slice(0, length);
}

/**
 * An id for a new element. Ids key the stored counts, so they are random rather than numbered: a
 * new line must never inherit the history of one that was deleted.
 */
export function newId(profile: Profile, kind: ElementKind, random: (length: number) => string = randomHex): string {
  const taken = new Set(elements(profile).map((element) => element.value.id));
  for (;;) {
    const id = `${ID_PREFIX[kind]}-${random(6)}`;
    if (!taken.has(id)) return id;
  }
}

export function countOf(profile: Profile, kind: ElementKind): number {
  return elements(profile).filter((element) => element.kind === kind).length;
}

/** How many of a kind a profile can hold: the server's limits. */
const LIMIT: Record<ElementKind, number> = {
  line: MAX_ELEMENTS,
  detection: MAX_ELEMENTS,
  exclusion: MAX_ELEMENTS,
  stop: MAX_ELEMENTS,
  calibration: 1,
  check: MAX_CHECKS,
};

export const limitOf = (kind: ElementKind): number => LIMIT[kind];

export const isFull = (profile: Profile, kind: ElementKind): boolean => countOf(profile, kind) >= LIMIT[kind];

// --- problems ------------------------------------------------------------------------------------

export interface Problem {
  /** "error" stops a save; "warning" is worth knowing but does not. */
  level: "error" | "warning";
  ref: ElementRef;
  message: string;
}

/** The ground point a counting line is judged at: where it is across the road. */
const lineMiddle = (line: LineConfig): Point => [(line.a[0] + line.b[0]) / 2, (line.a[1] + line.b[1]) / 2];

/** What is wrong with a profile, or worth a second look. Errors are what the server would refuse. */
export function problems(profile: Profile): Problem[] {
  const found: Problem[] = [];
  const name = (kind: ElementKind, id: string) => displayName(profile, { kind, id });

  for (const line of profile.lines) {
    const ref: ElementRef = { kind: "line", id: line.id };
    const length = Math.hypot(line.a[0] - line.b[0], line.a[1] - line.b[1]);
    if (length === 0) {
      found.push({ level: "error", ref, message: `${name("line", line.id)}: the two ends are in the same place` });
    }
    if (line.classes !== null && line.classes.length === 0) {
      found.push({ level: "error", ref, message: `${name("line", line.id)}: choose at least one kind of vehicle to count` });
    }
  }
  for (const [kind, zones] of [["detection", profile.detection_zones], ["exclusion", profile.exclusion_zones]] as const) {
    for (const zone of zones) {
      const problem = outlineProblem(zone.points);
      if (problem) found.push({ level: "error", ref: { kind, id: zone.id }, message: `${name(kind, zone.id)}: ${problem}` });
    }
  }

  for (const sign of profile.stop_signs) {
    found.push(...stopSignProblems(profile, sign, name("stop", sign.id)));
  }
  if (profile.calibration) {
    const ref: ElementRef = { kind: "calibration", id: CALIBRATION_ID };
    for (const message of calibrationProblems(profile.calibration)) {
      found.push({ level: "error", ref, message: `${name("calibration", CALIBRATION_ID)}: ${message}` });
    }
    for (const check of profile.calibration.checks) {
      for (const message of checkProblems(check)) {
        found.push({ level: "error", ref: { kind: "check", id: check.id }, message: `${name("check", check.id)}: ${message}` });
      }
    }
  }

  // A line only counts what the pipeline looks at: inside a detection zone (if there are any), and
  // outside every exclusion zone.
  for (const line of profile.lines) {
    const ref: ElementRef = { kind: "line", id: line.id };
    const middle = lineMiddle(line);
    const excluding = profile.exclusion_zones.find((zone) => pointInPolygon(middle, zone.points));
    if (excluding) {
      found.push({
        level: "warning",
        ref,
        message: `${name("line", line.id)} lies inside ${name("exclusion", excluding.id)}, where vehicles are ignored, so it will not count anything`,
      });
    } else if (profile.detection_zones.length > 0 && !profile.detection_zones.some((zone) => pointInPolygon(middle, zone.points))) {
      found.push({
        level: "warning",
        ref,
        message: `${name("line", line.id)} lies outside the detection zones, so it will not count anything`,
      });
    }
  }
  return found;
}

/** What is wrong with one stop sign, or worth a second look. */
function stopSignProblems(profile: Profile, sign: StopSignConfig, label: string): Problem[] {
  const ref: ElementRef = { kind: "stop", id: sign.id };
  const found: Problem[] = [];
  const error = (message: string) => found.push({ level: "error", ref, message: `${label}: ${message}` });
  const warning = (message: string) => found.push({ level: "warning", ref, message: `${label}: ${message}` });

  const flat = sign.a[0] === sign.b[0] && sign.a[1] === sign.b[1];
  if (flat) error("the two ends of the stop line are in the same place");
  const outline = outlineProblem(sign.approach);
  if (outline) error(`the approach zone: ${outline}`);
  if (sign.classes.length === 0) error("choose at least one kind of vehicle that has to stop");
  for (const [field, limit] of Object.entries(STOP_NUMBERS) as [StopNumber, (typeof STOP_NUMBERS)[StopNumber]][]) {
    const value = sign[field];
    if (!Number.isFinite(value) || value < limit.min || value > limit.max) {
      error(`${limit.label} must be between ${limit.min} and ${limit.max}`);
    }
  }
  if (flat || outline) return found; // the checks below need a line and a zone

  const centre = centreOf(sign.approach);
  const middle: Point = [(sign.a[0] + sign.b[0]) / 2, (sign.a[1] + sign.b[1]) / 2];
  if (sideOfLine(sign.a, sign.b, centre) === sign.direction) {
    warning("the approach zone is on the far side of the stop line, the side the arrow points to, so vehicles would be judged after the line. Flip the direction, or move the zone");
  } else if (distanceToPolygon(middle, sign.approach) > FAR_FROM_THE_ZONE) {
    warning("the stop line is a long way from the approach zone, which should end at the line");
  }
  const excluding = profile.exclusion_zones.find((zone) => pointInPolygon(centre, zone.points));
  if (excluding) {
    warning(`the approach zone lies inside ${displayName(profile, { kind: "exclusion", id: excluding.id })}, where vehicles are ignored, so it will not judge anything`);
  } else if (profile.detection_zones.length > 0 && !profile.detection_zones.some((zone) => pointInPolygon(centre, zone.points))) {
    warning("the approach zone lies outside the detection zones, so it will not judge anything");
  }
  return found;
}

/** A stop line further than this from its approach zone (a fraction of the picture) is probably misplaced. */
const FAR_FROM_THE_ZONE = 0.1;

export const hasErrors = (list: readonly Problem[]): boolean => list.some((problem) => problem.level === "error");

/** A zone's area as a share of the picture, in whole percent (never 0 for a zone that exists). */
export function areaPercent(zone: ZoneConfig): number {
  const area = polygonArea(zone.points);
  return area < MIN_ZONE_AREA ? 0 : Math.max(1, Math.round(area * 100));
}

/** The same value with every object's keys in sorted order, so that two copies compare equal as text. */
function withSortedKeys(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(withSortedKeys);
  if (value !== null && typeof value === "object") {
    return Object.fromEntries(
      Object.entries(value as Record<string, unknown>)
        .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
        .map(([key, member]) => [key, withSortedKeys(member)]),
    );
  }
  return value;
}

/** Whether two profiles say the same thing (key order does not matter). */
export const sameProfile = (a: Profile, b: Profile): boolean =>
  JSON.stringify(withSortedKeys(a)) === JSON.stringify(withSortedKeys(b));
