/**
 * Plane geometry for the zone editor. Points are `[x, y]` fractions of the picture (0 to 1) with y
 * pointing down. The outline checks are a port of backend/trafficpi/core/geometry.py, step for
 * step, and both are tested against backend/tests/data/polygon_cases.json: the editor must agree
 * with the server about what a usable outline is, or "Save" would fail for no visible reason.
 */

export type Point = [number, number];

export interface Size {
  width: number;
  height: number;
}

/** A zone is too small to matter below this fraction of the picture (the server's MIN_ZONE_AREA). */
export const MIN_ZONE_AREA = 1e-4;

export const clamp01 = (value: number): number => Math.min(1, Math.max(0, value));

export const clampPoint = (p: Point): Point => [clamp01(p[0]), clamp01(p[1])];

export const distance = (a: Point, b: Point): number => Math.hypot(a[0] - b[0], a[1] - b[1]);

/** A point in fractions of the picture, as pixels of a picture `size` big. */
export const toPixels = (p: Point, size: Size): Point => [p[0] * size.width, p[1] * size.height];

/** A pixel position on a picture `size` big, as fractions; clamped to the picture. */
export const toFraction = (px: Point, size: Size): Point =>
  size.width > 0 && size.height > 0 ? clampPoint([px[0] / size.width, px[1] / size.height]) : [0, 0];

/** The cross product of `a - o` and `b - o`: positive when `b` is to the right of the ray `o -> a`. */
const cross = (o: Point, a: Point, b: Point): number => (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0]);

export function closestPointOnSegment(p: Point, a: Point, b: Point): Point {
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const lengthSquared = dx * dx + dy * dy;
  if (lengthSquared === 0) return a;
  const t = Math.min(1, Math.max(0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / lengthSquared));
  return [a[0] + t * dx, a[1] + t * dy];
}

export const distanceToSegment = (p: Point, a: Point, b: Point): number => distance(p, closestPointOnSegment(p, a, b));

export function boundsOf(points: readonly Point[]): { x0: number; y0: number; x1: number; y1: number } {
  const xs = points.map((p) => p[0]);
  const ys = points.map((p) => p[1]);
  return { x0: Math.min(...xs), y0: Math.min(...ys), x1: Math.max(...xs), y1: Math.max(...ys) };
}

/** The enclosed area as a fraction of the picture (the shoelace formula); 0 for a flat outline. */
export function polygonArea(points: readonly Point[]): number {
  let twice = 0;
  points.forEach((p, i) => {
    const q = points[(i + 1) % points.length]!;
    twice += p[0] * q[1] - q[0] * p[1];
  });
  return Math.abs(twice) / 2;
}

const EPS = 1e-12;

function onSegment(a: Point, b: Point, p: Point): boolean {
  if (Math.abs(cross(a, b, p)) > EPS) return false;
  return (
    Math.min(a[0], b[0]) - EPS <= p[0] &&
    p[0] <= Math.max(a[0], b[0]) + EPS &&
    Math.min(a[1], b[1]) - EPS <= p[1] &&
    p[1] <= Math.max(a[1], b[1]) + EPS
  );
}

/** Whether `p` is inside the outline or on its boundary. */
export function pointInPolygon(p: Point, points: readonly Point[]): boolean {
  const [x, y] = p;
  let inside = false;
  for (let i = 0; i < points.length; i += 1) {
    const a = points[i]!;
    const b = points[(i + 1) % points.length]!;
    if (onSegment(a, b, p)) return true;
    if (a[1] > y !== b[1] > y) {
      const xAtY = a[0] + ((y - a[1]) * (b[0] - a[0])) / (b[1] - a[1]);
      if (x < xAtY) inside = !inside;
    }
  }
  return inside;
}

/** Whether the segments `a-b` and `c-d` have any point in common. */
function segmentsTouch(a: Point, b: Point, c: Point, d: Point): boolean {
  const d1 = cross(a, b, c);
  const d2 = cross(a, b, d);
  const d3 = cross(c, d, a);
  const d4 = cross(c, d, b);
  if (d1 * d2 < 0 && d3 * d4 < 0) return true; // each segment straddles the other's line
  return onSegment(a, b, c) || onSegment(a, b, d) || onSegment(c, d, a) || onSegment(c, d, b);
}

/** Whether the edges `a -> corner` and `corner -> c` run along each other, overlapping. */
function doublesBack(a: Point, corner: Point, c: Point): boolean {
  if (Math.abs(cross(a, corner, c)) > EPS) return false;
  return (a[0] - corner[0]) * (c[0] - corner[0]) + (a[1] - corner[1]) * (c[1] - corner[1]) > 0;
}

/**
 * Whether the outline avoids itself: no two edges cross or touch, no point is repeated and no edge
 * doubles back. Neighbouring edges meet at their shared corner and nowhere else.
 */
export function isSimplePolygon(points: readonly Point[]): boolean {
  const n = points.length;
  for (let i = 0; i < n; i += 1) {
    const a = points[i]!;
    const b = points[(i + 1) % n]!;
    if (a[0] === b[0] && a[1] === b[1]) return false;
    for (let j = i + 1; j < n; j += 1) {
      const c = points[j]!;
      const d = points[(j + 1) % n]!;
      if (j === i + 1) {
        if (doublesBack(a, b, d)) return false;
      } else if (i === 0 && j === n - 1) {
        if (doublesBack(b, a, c)) return false;
      } else if (segmentsTouch(a, b, c, d)) {
        return false;
      }
    }
  }
  return true;
}

/** Why an outline cannot be saved, in words; null when it is fine. */
export function outlineProblem(points: readonly Point[]): string | null {
  if (points.length < 3) return "needs at least three points";
  if (!isSimplePolygon(points)) return "the outline crosses itself, repeats a point or is flat";
  if (polygonArea(points) < MIN_ZONE_AREA) return "the zone is too small to matter";
  return null;
}
