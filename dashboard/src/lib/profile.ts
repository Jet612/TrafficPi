/**
 * The camera profile as the zone editor sees it: the lines and zones in one document, plus what can
 * be said about them without asking the server. The shape comes from the API (generated types);
 * the server stays the judge of what can be saved, and `problems` only gives early, readable notice.
 */
import { CLASS_ORDER } from "./classes";
import { MIN_ZONE_AREA, outlineProblem, pointInPolygon, polygonArea, type Point } from "./geometry";
import type { LineConfig, Profile, ZoneConfig } from "./types";

export type ElementKind = "line" | "detection" | "exclusion";

export interface ElementRef {
  kind: ElementKind;
  id: string;
}

export type Element =
  | { kind: "line"; value: LineConfig }
  | { kind: "detection" | "exclusion"; value: ZoneConfig };

export const SCHEMA_VERSION = 1;
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

export const KIND_LABEL: Record<ElementKind, string> = {
  line: "Counting line",
  detection: "Detection zone",
  exclusion: "Exclusion zone",
};

export function emptyProfile(): Profile {
  return { schema_version: SCHEMA_VERSION, lines: [], detection_zones: [], exclusion_zones: [] };
}

export const sameRef = (a: ElementRef | null | undefined, b: ElementRef | null | undefined): boolean =>
  a === b || (!!a && !!b && a.kind === b.kind && a.id === b.id);

/** Every element, zones first (they sit underneath), then lines. This is also the drawing order. */
export function elements(profile: Profile): Element[] {
  return [
    ...profile.detection_zones.map((value): Element => ({ kind: "detection", value })),
    ...profile.exclusion_zones.map((value): Element => ({ kind: "exclusion", value })),
    ...profile.lines.map((value): Element => ({ kind: "line", value })),
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

const ID_PREFIX: Record<ElementKind, string> = { line: "line", detection: "area", exclusion: "ignore" };

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

export const isFull = (profile: Profile, kind: ElementKind): boolean => countOf(profile, kind) >= MAX_ELEMENTS;

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
