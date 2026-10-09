/**
 * What the zone editor can do to a profile, as pure functions: each takes a profile and returns a
 * new one, or the very same object when nothing would change (so a no-op is cheap to spot and never
 * becomes an undo step). Points are fractions of the picture and always stay inside it.
 */
import { boundsOf, clamp01, clampPoint, type Point } from "./geometry";
import {
  COUNTABLE_CLASSES,
  DEFAULT_LINE_CLASSES,
  MAX_NAME_LENGTH,
  MAX_POINTS,
  countOf,
  isFull,
  newId,
  suggestName,
  type ElementKind,
  type ElementRef,
} from "./profile";
import type { LineConfig, Profile, ZoneConfig } from "./types";

export type ZoneKind = Exclude<ElementKind, "line">;

/** A zone has at least three corners; fewer is a line. */
export const MIN_CORNERS = 3;

export type Random = (length: number) => string;

export interface Added {
  profile: Profile;
  ref: ElementRef;
}

const zonesKey = (kind: ZoneKind) => (kind === "detection" ? "detection_zones" : "exclusion_zones");

function mapLine(profile: Profile, id: string, change: (line: LineConfig) => LineConfig): Profile {
  const index = profile.lines.findIndex((line) => line.id === id);
  if (index === -1) return profile;
  const current = profile.lines[index]!;
  const next = change(current);
  if (next === current) return profile;
  return { ...profile, lines: profile.lines.map((line, i) => (i === index ? next : line)) };
}

function mapZone(profile: Profile, kind: ZoneKind, id: string, change: (zone: ZoneConfig) => ZoneConfig): Profile {
  const key = zonesKey(kind);
  const index = profile[key].findIndex((zone) => zone.id === id);
  if (index === -1) return profile;
  const current = profile[key][index]!;
  const next = change(current);
  if (next === current) return profile;
  return { ...profile, [key]: profile[key].map((zone, i) => (i === index ? next : zone)) };
}

/** The points of an element, whatever its kind: the two ends of a line, or the corners of a zone. */
export function pointsOf(profile: Profile, ref: ElementRef): Point[] {
  if (ref.kind === "line") {
    const line = profile.lines.find((candidate) => candidate.id === ref.id);
    return line ? [line.a, line.b] : [];
  }
  return profile[zonesKey(ref.kind)].find((zone) => zone.id === ref.id)?.points ?? [];
}

const samePoint = (a: Point, b: Point): boolean => a[0] === b[0] && a[1] === b[1];

// --- adding --------------------------------------------------------------------------------------

/**
 * A new counting line across the middle of the picture, ready to be dragged into place. Each new
 * line starts a little lower than the last, so that they do not hide one another. Null when the
 * profile already has as many lines as it can hold.
 */
export function addLine(profile: Profile, random?: Random): Added | null {
  if (isFull(profile, "line")) return null;
  const y = 0.5 + 0.06 * (countOf(profile, "line") % 8);
  const line: LineConfig = {
    id: newId(profile, "line", random),
    name: suggestName(profile, "line"),
    a: [0.25, y],
    b: [0.75, y],
    classes: [...DEFAULT_LINE_CLASSES],
    point: "ground",
    margin: null,
    persistence: null,
  };
  return { profile: { ...profile, lines: [...profile.lines, line] }, ref: { kind: "line", id: line.id } };
}

/** Where a new zone of each kind starts: a detection zone over the road, an exclusion zone beside it. */
const FIRST_ZONE: Record<ZoneKind, readonly [number, number, number, number]> = {
  detection: [0.1, 0.3, 0.9, 0.9],
  exclusion: [0.1, 0.55, 0.3, 0.85],
};

/**
 * A new rectangular zone, ready to be dragged into shape. Later zones of a kind start somewhat
 * smaller (detection) or further along (exclusion) so that none is hidden by another. Null when the
 * profile already has as many zones of that kind as it can hold.
 */
export function addZone(profile: Profile, kind: ZoneKind, random?: Random): Added | null {
  if (isFull(profile, kind)) return null;
  const step = countOf(profile, kind) % 5;
  const [x0, y0, x1, y1] = FIRST_ZONE[kind];
  const [dx, dy] = kind === "detection" ? [0.04 * step, 0.03 * step] : [0.14 * step, 0];
  const points: Point[] =
    kind === "detection"
      ? [[x0 + dx, y0 + dy], [x1 - dx, y0 + dy], [x1 - dx, y1 - dy], [x0 + dx, y1 - dy]]
      : [[x0 + dx, y0], [x1 + dx, y0], [x1 + dx, y1], [x0 + dx, y1]];
  const zone: ZoneConfig = {
    id: newId(profile, kind, random),
    name: suggestName(profile, kind),
    points: points.map(clampPoint),
  };
  const key = zonesKey(kind);
  return { profile: { ...profile, [key]: [...profile[key], zone] }, ref: { kind, id: zone.id } };
}

// --- reshaping -----------------------------------------------------------------------------------

/** Move one end of a line (0 is `a`, 1 is `b`) or one corner of a zone to `to`, kept inside the picture. */
export function movePoint(profile: Profile, ref: ElementRef, index: number, to: Point): Profile {
  const target = clampPoint(to);
  if (ref.kind === "line") {
    return mapLine(profile, ref.id, (line) => {
      if (index !== 0 && index !== 1) return line;
      const current = index === 0 ? line.a : line.b;
      if (samePoint(current, target)) return line;
      return index === 0 ? { ...line, a: target } : { ...line, b: target };
    });
  }
  return mapZone(profile, ref.kind, ref.id, (zone) => {
    const current = zone.points[index];
    if (current === undefined || samePoint(current, target)) return zone;
    return { ...zone, points: zone.points.map((p, i) => (i === index ? target : p)) };
  });
}

/**
 * Move a whole element by (dx, dy) fractions of the picture. It stops at the edge rather than being
 * squashed against it, so that its shape is kept.
 */
export function moveElement(profile: Profile, ref: ElementRef, dx: number, dy: number): Profile {
  const points = pointsOf(profile, ref);
  if (points.length === 0) return profile;
  const { x0, y0, x1, y1 } = boundsOf(points);
  const sx = Math.min(Math.max(dx, -x0), 1 - x1);
  const sy = Math.min(Math.max(dy, -y0), 1 - y1);
  if (sx === 0 && sy === 0) return profile;
  const shift = (p: Point): Point => [clamp01(p[0] + sx), clamp01(p[1] + sy)];
  if (ref.kind === "line") return mapLine(profile, ref.id, (line) => ({ ...line, a: shift(line.a), b: shift(line.b) }));
  return mapZone(profile, ref.kind, ref.id, (zone) => ({ ...zone, points: zone.points.map(shift) }));
}

/** Whether a zone can take another corner. */
export const canAddCorner = (profile: Profile, ref: ElementRef): boolean =>
  ref.kind !== "line" && pointsOf(profile, ref).length > 0 && pointsOf(profile, ref).length < MAX_POINTS;

/** Whether a zone can lose a corner and still be a zone. */
export const canRemoveCorner = (profile: Profile, ref: ElementRef): boolean =>
  ref.kind !== "line" && pointsOf(profile, ref).length > MIN_CORNERS;

/** Add a corner to a zone, after corner `afterIndex` (so it becomes corner `afterIndex + 1`). */
export function insertCorner(profile: Profile, ref: ElementRef, afterIndex: number, at: Point): Profile {
  if (ref.kind === "line" || !canAddCorner(profile, ref)) return profile;
  return mapZone(profile, ref.kind, ref.id, (zone) => {
    if (afterIndex < 0 || afterIndex >= zone.points.length) return zone;
    const index = afterIndex + 1;
    return { ...zone, points: [...zone.points.slice(0, index), clampPoint(at), ...zone.points.slice(index)] };
  });
}

/**
 * Add a corner in the middle of a zone's longest edge, which is how a zone gets more corners
 * without a pointer. Null when the zone cannot take another. The new corner's index comes with it.
 */
export function splitLongestEdge(profile: Profile, ref: ElementRef): { profile: Profile; index: number } | null {
  if (ref.kind === "line" || !canAddCorner(profile, ref)) return null;
  const points = pointsOf(profile, ref);
  let longest = 0;
  let longestLength = -1;
  points.forEach((p, i) => {
    const q = points[(i + 1) % points.length]!;
    const length = Math.hypot(p[0] - q[0], p[1] - q[1]);
    if (length > longestLength) {
      longest = i;
      longestLength = length;
    }
  });
  return { profile: insertCorner(profile, ref, longest, edgeMiddle(points, longest)), index: longest + 1 };
}

/** Take a corner out of a zone. A zone keeps at least three. */
export function removeCorner(profile: Profile, ref: ElementRef, index: number): Profile {
  if (ref.kind === "line" || !canRemoveCorner(profile, ref)) return profile;
  return mapZone(profile, ref.kind, ref.id, (zone) =>
    index < 0 || index >= zone.points.length ? zone : { ...zone, points: zone.points.filter((_, i) => i !== index) },
  );
}

/** The point halfway along the edge that starts at corner `index` (the last edge closes the outline). */
export function edgeMiddle(points: readonly Point[], index: number): Point {
  const a = points[index]!;
  const b = points[(index + 1) % points.length]!;
  return [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
}

// --- properties ----------------------------------------------------------------------------------

export function renameElement(profile: Profile, ref: ElementRef, name: string): Profile {
  const next = name.slice(0, MAX_NAME_LENGTH);
  if (ref.kind === "line") return mapLine(profile, ref.id, (line) => (line.name === next ? line : { ...line, name: next }));
  return mapZone(profile, ref.kind, ref.id, (zone) => (zone.name === next ? zone : { ...zone, name: next }));
}

/**
 * Tick or untick a kind of vehicle on a line. A line that counts every kind (`classes` null) starts
 * from the kinds the editor lists. The result keeps the usual order, then any other kinds a profile
 * written by hand may have named.
 */
export function toggleLineClass(profile: Profile, id: string, name: string): Profile {
  return mapLine(profile, id, (line) => {
    const current = line.classes ?? [...COUNTABLE_CLASSES];
    const next = current.includes(name) ? current.filter((kind) => kind !== name) : [...current, name];
    const known = COUNTABLE_CLASSES.filter((kind) => next.includes(kind));
    const others = next.filter((kind) => !COUNTABLE_CLASSES.includes(kind));
    return { ...line, classes: [...known, ...others] };
  });
}

/** Turn a line round: what counted as forward now counts as reverse, and the other way about. */
export function flipLine(profile: Profile, id: string): Profile {
  return mapLine(profile, id, (line) => ({ ...line, a: line.b, b: line.a }));
}

export function deleteElement(profile: Profile, ref: ElementRef): Profile {
  if (ref.kind === "line") {
    return profile.lines.some((line) => line.id === ref.id)
      ? { ...profile, lines: profile.lines.filter((line) => line.id !== ref.id) }
      : profile;
  }
  const key = zonesKey(ref.kind);
  return profile[key].some((zone) => zone.id === ref.id)
    ? { ...profile, [key]: profile[key].filter((zone) => zone.id !== ref.id) }
    : profile;
}
