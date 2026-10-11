/**
 * What the zone editor can do to a profile, as pure functions: each takes a profile and returns a
 * new one, or the very same object when nothing would change (so a no-op is cheap to spot and never
 * becomes an undo step). Points are fractions of the picture and always stay inside it.
 */
import {
  CALIBRATION_ID,
  MAX_LENGTH_M,
  MAX_WIDTH_M,
  MIN_CHECK_M,
  MIN_LENGTH_M,
  MIN_WIDTH_M,
  newCalibration,
  newCheck,
} from "./calibration";
import { boundsOf, clamp01, clampPoint, type Point } from "./geometry";
import {
  COUNTABLE_CLASSES,
  DEFAULT_LINE_CLASSES,
  DEFAULT_STOP_CLASSES,
  MAX_NAME_LENGTH,
  MAX_POINTS,
  STOPPABLE_CLASSES,
  STOP_DEFAULTS,
  STOP_NUMBERS,
  countOf,
  isFull,
  newId,
  suggestName,
  type ElementRef,
  type StopNumber,
} from "./profile";
import type { CalibrationConfig, CheckConfig, LineConfig, Profile, StopSignConfig, ZoneConfig } from "./types";

/** The kinds of element that are just an outline. */
export type ZoneKind = "detection" | "exclusion";

/** The kinds of element that are an outline whose corners can be added and taken away. */
const isOutline = (kind: ElementRef["kind"]): kind is ZoneKind | "stop" => kind === "detection" || kind === "exclusion" || kind === "stop";

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

function mapStop(profile: Profile, id: string, change: (sign: StopSignConfig) => StopSignConfig): Profile {
  const index = profile.stop_signs.findIndex((sign) => sign.id === id);
  if (index === -1) return profile;
  const current = profile.stop_signs[index]!;
  const next = change(current);
  if (next === current) return profile;
  return { ...profile, stop_signs: profile.stop_signs.map((sign, i) => (i === index ? next : sign)) };
}

function mapCalibration(profile: Profile, change: (calibration: CalibrationConfig) => CalibrationConfig): Profile {
  const current = profile.calibration;
  if (current === null) return profile;
  const next = change(current);
  return next === current ? profile : { ...profile, calibration: next };
}

function mapCheck(profile: Profile, id: string, change: (check: CheckConfig) => CheckConfig): Profile {
  return mapCalibration(profile, (calibration) => {
    const index = calibration.checks.findIndex((check) => check.id === id);
    if (index === -1) return calibration;
    const current = calibration.checks[index]!;
    const next = change(current);
    if (next === current) return calibration;
    return { ...calibration, checks: calibration.checks.map((check, i) => (i === index ? next : check)) };
  });
}

/**
 * The points of an element, whatever its kind: the two ends of a line or of a known distance, the
 * corners of a zone or of the speed calibration's rectangle, or for a stop sign the corners of its
 * approach zone followed by the two ends of its stop line.
 */
export function pointsOf(profile: Profile, ref: ElementRef): Point[] {
  if (ref.kind === "calibration") return profile.calibration ? [...profile.calibration.corners] : [];
  if (ref.kind === "check") {
    const check = profile.calibration?.checks.find((candidate) => candidate.id === ref.id);
    return check ? [check.a, check.b] : [];
  }
  if (ref.kind === "line") {
    const line = profile.lines.find((candidate) => candidate.id === ref.id);
    return line ? [line.a, line.b] : [];
  }
  if (ref.kind === "stop") {
    const sign = profile.stop_signs.find((candidate) => candidate.id === ref.id);
    return sign ? [...sign.approach, sign.a, sign.b] : [];
  }
  return profile[zonesKey(ref.kind)].find((zone) => zone.id === ref.id)?.points ?? [];
}

/**
 * How many of an element's first points are the corners of an outline that can grow and shrink: all
 * of a zone's, none of a line's, and a stop sign's approach zone's (its last two points are the
 * ends of the stop line). The four corners of the calibration's rectangle are always four.
 */
export function cornerCount(profile: Profile, ref: ElementRef): number {
  if (ref.kind === "line" || ref.kind === "calibration" || ref.kind === "check") return 0;
  if (ref.kind === "stop") return profile.stop_signs.find((sign) => sign.id === ref.id)?.approach.length ?? 0;
  return pointsOf(profile, ref).length;
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

/**
 * A new stop sign: an approach zone on the left of the middle of the picture and, at its right
 * edge, a stop line that vehicles drive across from left to right. The line reaches a little past
 * the zone at both ends, as a stop line does across a road, so that no handle sits on another.
 * Later signs start further down, so that they do not hide one another. Null when the profile has
 * as many as it can hold.
 */
export function addStop(profile: Profile, random?: Random): Added | null {
  if (isFull(profile, "stop")) return null;
  const drop = 0.05 * (countOf(profile, "stop") % 5);
  const top = 0.45 + drop;
  const bottom = 0.75 + drop;
  const sign: StopSignConfig = {
    id: newId(profile, "stop", random),
    name: suggestName(profile, "stop"),
    approach: [
      [0.15, top],
      [0.5, top],
      [0.5, bottom],
      [0.15, bottom],
    ],
    // the line is drawn from the bottom to the top, so that its right-hand side is where vehicles go
    a: [0.5, bottom + 0.05],
    b: [0.5, top - 0.05],
    direction: 1,
    classes: [...DEFAULT_STOP_CLASSES],
    ...STOP_DEFAULTS,
  };
  return { profile: { ...profile, stop_signs: [...profile.stop_signs, sign] }, ref: { kind: "stop", id: sign.id } };
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

/**
 * Move one end of a line (0 is `a`, 1 is `b`) or one corner of a zone to `to`, kept inside the
 * picture. For a stop sign the corners of its approach zone come first, then the ends of its line.
 */
export function movePoint(profile: Profile, ref: ElementRef, index: number, to: Point): Profile {
  const target = clampPoint(to);
  if (ref.kind === "calibration") {
    return mapCalibration(profile, (calibration) => {
      const current = calibration.corners[index];
      if (current === undefined || samePoint(current, target)) return calibration;
      return { ...calibration, corners: calibration.corners.map((p, i) => (i === index ? target : p)) };
    });
  }
  if (ref.kind === "check") {
    return mapCheck(profile, ref.id, (check) => {
      if (index !== 0 && index !== 1) return check;
      if (samePoint(index === 0 ? check.a : check.b, target)) return check;
      return index === 0 ? { ...check, a: target } : { ...check, b: target };
    });
  }
  if (ref.kind === "stop") {
    return mapStop(profile, ref.id, (sign) => {
      const corners = sign.approach.length;
      if (index >= 0 && index < corners) {
        if (samePoint(sign.approach[index]!, target)) return sign;
        return { ...sign, approach: sign.approach.map((p, i) => (i === index ? target : p)) };
      }
      if (index !== corners && index !== corners + 1) return sign;
      const current = index === corners ? sign.a : sign.b;
      if (samePoint(current, target)) return sign;
      return index === corners ? { ...sign, a: target } : { ...sign, b: target };
    });
  }
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
  if (ref.kind === "calibration") return mapCalibration(profile, (calibration) => ({ ...calibration, corners: calibration.corners.map(shift) }));
  if (ref.kind === "check") return mapCheck(profile, ref.id, (check) => ({ ...check, a: shift(check.a), b: shift(check.b) }));
  if (ref.kind === "stop") {
    return mapStop(profile, ref.id, (sign) => ({ ...sign, approach: sign.approach.map(shift), a: shift(sign.a), b: shift(sign.b) }));
  }
  if (ref.kind === "line") return mapLine(profile, ref.id, (line) => ({ ...line, a: shift(line.a), b: shift(line.b) }));
  return mapZone(profile, ref.kind, ref.id, (zone) => ({ ...zone, points: zone.points.map(shift) }));
}

/** Whether a zone (or a stop sign's approach zone) can take another corner. */
export const canAddCorner = (profile: Profile, ref: ElementRef): boolean =>
  cornerCount(profile, ref) > 0 && cornerCount(profile, ref) < MAX_POINTS;

/**
 * Whether a zone can lose a corner and still be a zone. With an index, whether that point is a
 * corner at all (the ends of a stop sign's line are not).
 */
export const canRemoveCorner = (profile: Profile, ref: ElementRef, index?: number): boolean =>
  cornerCount(profile, ref) > MIN_CORNERS && (index === undefined || (index >= 0 && index < cornerCount(profile, ref)));

/** Add a corner to a zone, after corner `afterIndex` (so it becomes corner `afterIndex + 1`). */
export function insertCorner(profile: Profile, ref: ElementRef, afterIndex: number, at: Point): Profile {
  if (!isOutline(ref.kind) || !canAddCorner(profile, ref)) return profile;
  const grow = (points: [number, number][]): [number, number][] | null => {
    if (afterIndex < 0 || afterIndex >= points.length) return null;
    const index = afterIndex + 1;
    return [...points.slice(0, index), clampPoint(at), ...points.slice(index)];
  };
  if (ref.kind === "stop") {
    return mapStop(profile, ref.id, (sign) => {
      const approach = grow(sign.approach);
      return approach ? { ...sign, approach } : sign;
    });
  }
  return mapZone(profile, ref.kind, ref.id, (zone) => {
    const points = grow(zone.points);
    return points ? { ...zone, points } : zone;
  });
}

/**
 * Add a corner in the middle of a zone's longest edge, which is how a zone gets more corners
 * without a pointer. Null when the zone cannot take another. The new corner's index comes with it.
 */
export function splitLongestEdge(profile: Profile, ref: ElementRef): { profile: Profile; index: number } | null {
  if (!isOutline(ref.kind) || !canAddCorner(profile, ref)) return null;
  const points = pointsOf(profile, ref).slice(0, cornerCount(profile, ref));
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
  if (!isOutline(ref.kind) || !canRemoveCorner(profile, ref, index)) return profile;
  if (ref.kind === "stop") {
    return mapStop(profile, ref.id, (sign) => ({ ...sign, approach: sign.approach.filter((_, i) => i !== index) }));
  }
  return mapZone(profile, ref.kind, ref.id, (zone) => ({ ...zone, points: zone.points.filter((_, i) => i !== index) }));
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
  if (ref.kind === "calibration") return profile; // there is one, and it has no name of its own
  if (ref.kind === "check") return mapCheck(profile, ref.id, (check) => (check.name === next ? check : { ...check, name: next }));
  if (ref.kind === "stop") return mapStop(profile, ref.id, (sign) => (sign.name === next ? sign : { ...sign, name: next }));
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

/** Turn the way through a stop sign round: what was the approach is now the far side. */
export function flipStop(profile: Profile, id: string): Profile {
  return mapStop(profile, id, (sign) => ({ ...sign, direction: sign.direction === 1 ? -1 : 1 }));
}

/** Tick or untick a kind of vehicle that has to stop, keeping the usual order of the kinds. */
export function toggleStopClass(profile: Profile, id: string, name: string): Profile {
  return mapStop(profile, id, (sign) => {
    const next = sign.classes.includes(name) ? sign.classes.filter((kind) => kind !== name) : [...sign.classes, name];
    const known = STOPPABLE_CLASSES.filter((kind) => next.includes(kind));
    const others = next.filter((kind) => !STOPPABLE_CLASSES.includes(kind));
    return { ...sign, classes: [...known, ...others] };
  });
}

/**
 * Change one of a stop sign's numbers. A number that is not a number, or is outside what the server
 * takes, is not applied (the form says so); thousandths are as fine as any of them needs to be.
 */
export function setStopNumber(profile: Profile, id: string, field: StopNumber, value: number): Profile {
  const { min, max } = STOP_NUMBERS[field];
  if (!Number.isFinite(value) || value < min || value > max) return profile;
  const rounded = Math.round(value * 1000) / 1000;
  return mapStop(profile, id, (sign) => (sign[field] === rounded ? sign : { ...sign, [field]: rounded }));
}

/** Put a stop sign's numbers back to what a stop is to begin with. */
export function resetStopNumbers(profile: Profile, id: string): Profile {
  return mapStop(profile, id, (sign) =>
    (Object.keys(STOP_DEFAULTS) as StopNumber[]).every((field) => sign[field] === STOP_DEFAULTS[field]) ? sign : { ...sign, ...STOP_DEFAULTS },
  );
}

// --- the speed calibration ---------------------------------------------------------------------

/**
 * A speed calibration: a rectangle laid on the road in the picture, with a width and a length for
 * the owner to correct. A profile has at most one; null when it already has.
 */
export function addCalibration(profile: Profile): Added | null {
  if (profile.calibration !== null) return null;
  return { profile: { ...profile, calibration: newCalibration() }, ref: { kind: "calibration", id: CALIBRATION_ID } };
}

/** The width (across the road) or the length (along it) of the calibration's rectangle, in metres. One outside what the server takes is not applied. */
export function setCalibrationSize(profile: Profile, field: "width_m" | "length_m", value: number): Profile {
  const [min, max] = field === "width_m" ? [MIN_WIDTH_M, MAX_WIDTH_M] : [MIN_LENGTH_M, MAX_LENGTH_M];
  if (!Number.isFinite(value) || value < min || value > max) return profile;
  const rounded = Math.round(value * 1000) / 1000;
  return mapCalibration(profile, (calibration) => (calibration[field] === rounded ? calibration : { ...calibration, [field]: rounded }));
}

/** A known distance to test the calibration with, along the road to begin with. Null with no calibration, or when there are as many as can be held. */
export function addCheck(profile: Profile, random?: Random): Added | null {
  const { calibration } = profile;
  if (calibration === null || isFull(profile, "check")) return null;
  const check = newCheck(newId(profile, "check", random), suggestName(profile, "check"), calibration);
  return {
    profile: { ...profile, calibration: { ...calibration, checks: [...calibration.checks, check] } },
    ref: { kind: "check", id: check.id },
  };
}

/** How long a known distance is, in metres. One outside what the server takes is not applied. */
export function setCheckLength(profile: Profile, id: string, value: number): Profile {
  if (!Number.isFinite(value) || value < MIN_CHECK_M || value > MAX_LENGTH_M) return profile;
  const rounded = Math.round(value * 1000) / 1000;
  return mapCheck(profile, id, (check) => (check.length_m === rounded ? check : { ...check, length_m: rounded }));
}

export function deleteElement(profile: Profile, ref: ElementRef): Profile {
  if (ref.kind === "calibration") return profile.calibration === null ? profile : { ...profile, calibration: null };
  if (ref.kind === "check") {
    return mapCalibration(profile, (calibration) =>
      calibration.checks.some((check) => check.id === ref.id)
        ? { ...calibration, checks: calibration.checks.filter((check) => check.id !== ref.id) }
        : calibration,
    );
  }
  if (ref.kind === "stop") {
    return profile.stop_signs.some((sign) => sign.id === ref.id)
      ? { ...profile, stop_signs: profile.stop_signs.filter((sign) => sign.id !== ref.id) }
      : profile;
  }
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
