import { classColor } from "./classes";
import { speedShort, type SpeedUnits } from "./speed";
import type { LiveLine, LiveStopSign, LiveTrack, LiveZone } from "./types";

export interface Size {
  width: number;
  height: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type Point = [number, number];

export interface OverlayOptions {
  boxes: boolean;
  labels: boolean;
  trails: boolean;
  lines: boolean;
  zones: boolean;
}

export const DEFAULT_OVERLAY: OverlayOptions = { boxes: true, labels: true, trails: true, lines: true, zones: true };

/** A box in fractions of the frame, as pixels of the canvas. */
export function boxRect(box: LiveTrack["box"], size: Size): Rect {
  const [x0, y0, x1, y1] = box;
  return { x: x0 * size.width, y: y0 * size.height, w: (x1 - x0) * size.width, h: (y1 - y0) * size.height };
}

/** Where a vehicle meets the road: the middle of the bottom edge of its box (as fractions). */
export function groundPoint(box: LiveTrack["box"]): Point {
  return [(box[0] + box[2]) / 2, box[3]];
}

/**
 * The arrow that shows which way counts as "forward" on a line: from its middle, at right angles,
 * towards the right-hand side of a -> b (y points down, as in the backend's geometry).
 */
export function directionArrow(line: Pick<LiveLine, "a" | "b">, size: Size, length = 26): { from: Point; to: Point } {
  const a: Point = [line.a[0] * size.width, line.a[1] * size.height];
  const b: Point = [line.b[0] * size.width, line.b[1] * size.height];
  const dx = b[0] - a[0];
  const dy = b[1] - a[1];
  const norm = Math.hypot(dx, dy) || 1;
  const from: Point = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
  return { from, to: [from[0] - (dy / norm) * length, from[1] + (dx / norm) * length] };
}

/** The two short strokes that make the tip of an arrow from `from` to `to`. */
export function arrowHead(from: Point, to: Point, length = 8, spread = 0.55): [Point, Point] {
  const angle = Math.atan2(to[1] - from[1], to[0] - from[0]);
  const wing = (turn: number): Point => [
    to[0] - length * Math.cos(angle + turn),
    to[1] - length * Math.sin(angle + turn),
  ];
  return [wing(spread), wing(-spread)];
}

/** Black or white, whichever reads better on a "#rrggbb" fill. */
export function chipTextColor(hex: string): "#000000" | "#ffffff" {
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) return "#ffffff";
  const value = Number.parseInt(match[1]!, 16);
  const channel = (shift: number) => {
    const c = ((value >> shift) & 255) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const luminance = 0.2126 * channel(16) + 0.7152 * channel(8) + 0.0722 * channel(0);
  // White text wins when the fill is dark enough that it contrasts more than black does.
  return (1.05 / (luminance + 0.05) >= (luminance + 0.05) / 0.05 ? "#ffffff" : "#000000");
}

export interface Trail {
  label: string;
  points: { p: Point; t: number }[];
}

/** Add each track's ground point to its trail, and forget old points and vanished tracks. */
export function updateTrails(trails: Map<number, Trail>, tracks: readonly LiveTrack[], nowMs: number, maxAgeMs = 2000): void {
  const alive = new Set<number>();
  for (const track of tracks) {
    if (!track.confirmed) continue;
    alive.add(track.id);
    const trail = trails.get(track.id) ?? { label: track.label, points: [] };
    trail.label = track.label;
    trail.points.push({ p: groundPoint(track.box), t: nowMs });
    trails.set(track.id, trail);
  }
  for (const [id, trail] of trails) {
    trail.points = trail.points.filter((point) => nowMs - point.t <= maxAgeMs);
    if (!alive.has(id) && trail.points.length === 0) trails.delete(id);
  }
}

/** The colours the canvas needs, as concrete strings (a canvas cannot read CSS variables). */
export interface OverlayColors {
  /** A class name to its "#rrggbb" colour; unknown classes use `other`. */
  forClass: (name: string) => string;
  line: string;
  chipEdge: string;
  /** The shading over an exclusion zone, which says "nothing here is looked at". */
  exclusionFill: string;
  /** A stop line, and the tint of the road before it where a stop counts. */
  stopLine: string;
  stopFill: string;
}

/** The drawing calls the overlay uses, so tests can record them. */
export type Canvas2D = Pick<
  CanvasRenderingContext2D,
  | "save" | "restore" | "clearRect" | "beginPath" | "moveTo" | "lineTo" | "stroke" | "fill"
  | "fillRect" | "strokeRect" | "fillText" | "measureText" | "setLineDash" | "closePath"
> & {
  strokeStyle: CanvasRenderingContext2D["strokeStyle"];
  fillStyle: CanvasRenderingContext2D["fillStyle"];
  lineWidth: number;
  font: string;
  globalAlpha: number;
  lineJoin: CanvasLineJoin;
  lineCap: CanvasLineCap;
  textBaseline: CanvasTextBaseline;
};

const LABEL_FONT = "600 11px system-ui, -apple-system, 'Segoe UI', sans-serif";
const COMPACT_FONT = "600 10px system-ui, -apple-system, 'Segoe UI', sans-serif";

/** A picture narrower than this (a phone) gets short labels, so that they do not pile up. */
export const COMPACT_BELOW_PX = 520;
/** A box narrower than this is too small to carry a label. */
const MIN_LABELLED_PX = 16;

/** The words on a vehicle's label: with an id and a score when there is room, else the kind; then its speed, if it has one. */
export function trackLabel(track: Pick<LiveTrack, "id" | "label" | "score" | "speed_ms">, compact: boolean, units?: SpeedUnits): string {
  const words = compact ? track.label : `#${track.id} ${track.label} ${Math.round(track.score * 100)}%`;
  // A speed is added when the camera is calibrated and this one can be believed: a measured speed, so no "about".
  return track.speed_ms !== undefined && units !== undefined ? `${words} ${speedShort(track.speed_ms, units, true)}` : words;
}

/** The words on a zone's label: its own name, or what kind of zone it is. */
export function zoneLabel(zone: Pick<LiveZone, "kind" | "name">): string {
  if (zone.kind === "exclusion") return zone.name ? `Ignored: ${zone.name}` : "Ignored area";
  return zone.name || "Detection zone";
}

function drawZones(ctx: Canvas2D, size: Size, zones: readonly LiveZone[], labels: boolean, colors: OverlayColors, compact: boolean): void {
  for (const zone of zones) {
    if (zone.points.length < 3) continue;
    const corners = zone.points.map((p): Point => [p[0] * size.width, p[1] * size.height]);
    const outline = () => {
      ctx.beginPath();
      corners.forEach(([x, y], index) => (index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
      ctx.closePath();
    };
    ctx.save();
    if (zone.kind === "exclusion") {
      ctx.fillStyle = colors.exclusionFill;
      outline();
      ctx.fill();
    }
    ctx.setLineDash([7, 5]);
    for (const [style, width] of [[colors.chipEdge, 4], [colors.line, 2]] as const) {
      ctx.strokeStyle = style;
      ctx.lineWidth = width;
      outline();
      ctx.stroke();
    }
    ctx.restore();
    if (labels) {
      const left = Math.min(...corners.map((c) => c[0]));
      const top = Math.min(...corners.map((c) => c[1]));
      drawChip(ctx, zoneLabel(zone), left + 2, Math.max(16, top - 4), colors.line, compact);
    }
  }
}

export interface OverlayScene {
  lines: readonly LiveLine[];
  zones: readonly LiveZone[];
  tracks: readonly LiveTrack[];
  stopSigns?: readonly LiveStopSign[];
  /** The units a vehicle's speed is written in on its label; none: speeds are not written. */
  units?: SpeedUnits;
}

/**
 * Stop signs: the road before the line tinted red (with the zones), and the line itself in red
 * with an arrow to the way through (with the lines).
 */
function drawStopSigns(
  ctx: Canvas2D,
  size: Size,
  signs: readonly LiveStopSign[],
  options: OverlayOptions,
  colors: OverlayColors,
  compact: boolean,
): void {
  for (const sign of signs) {
    if (options.zones && sign.approach.length >= 3) {
      const corners = sign.approach.map((p): Point => [p[0] * size.width, p[1] * size.height]);
      const outline = () => {
        ctx.beginPath();
        corners.forEach(([x, y], index) => (index === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y)));
        ctx.closePath();
      };
      ctx.save();
      ctx.fillStyle = colors.stopFill;
      outline();
      ctx.fill();
      ctx.setLineDash([7, 5]);
      for (const [style, width] of [[colors.chipEdge, 4], [colors.line, 2]] as const) {
        ctx.strokeStyle = style;
        ctx.lineWidth = width;
        outline();
        ctx.stroke();
      }
      ctx.restore();
    }
    if (!options.lines) continue;
    const ax = sign.a[0] * size.width;
    const ay = sign.a[1] * size.height;
    const bx = sign.b[0] * size.width;
    const by = sign.b[1] * size.height;
    // The way through is the right of a -> b for +1, and its left for -1.
    const through = sign.direction === 1 ? { a: sign.a, b: sign.b } : { a: sign.b, b: sign.a };
    const arrow = directionArrow(through, size);
    ctx.save();
    ctx.lineCap = "butt";
    for (const [style, width] of [[colors.chipEdge, 8], [colors.stopLine, 5]] as const) {
      ctx.strokeStyle = style;
      ctx.lineWidth = width;
      ctx.beginPath();
      ctx.moveTo(ax, ay);
      ctx.lineTo(bx, by);
      ctx.stroke();
    }
    ctx.lineCap = "round";
    for (const [style, width] of [[colors.chipEdge, 5], [colors.line, 2.5]] as const) {
      ctx.strokeStyle = style;
      ctx.lineWidth = width;
      ctx.beginPath();
      ctx.moveTo(arrow.from[0], arrow.from[1]);
      ctx.lineTo(arrow.to[0], arrow.to[1]);
      for (const wing of arrowHead(arrow.from, arrow.to)) {
        ctx.moveTo(arrow.to[0], arrow.to[1]);
        ctx.lineTo(wing[0], wing[1]);
      }
      ctx.stroke();
    }
    ctx.restore();
    if (options.labels && !compact) drawChip(ctx, sign.name || "Stop sign", Math.min(ax, bx) + 2, Math.max(16, Math.min(ay, by) - 4), colors.stopLine, compact);
  }
}

export function drawOverlay(
  ctx: Canvas2D,
  size: Size,
  scene: OverlayScene,
  options: OverlayOptions,
  trails: ReadonlyMap<number, Trail>,
  colors: OverlayColors,
): void {
  ctx.clearRect(0, 0, size.width, size.height);
  paintOverlay(ctx, size, scene, options, trails, colors);
}

/** Like `drawOverlay`, but on top of what is already drawn: the Trails card paints the paths first. */
export function paintOverlay(
  ctx: Canvas2D,
  size: Size,
  scene: OverlayScene,
  options: OverlayOptions,
  trails: ReadonlyMap<number, Trail>,
  colors: OverlayColors,
): void {
  const compact = size.width < COMPACT_BELOW_PX;
  ctx.lineJoin = "round";
  ctx.lineCap = "round";

  // A zone's name is long ("Ignored: Parked cars") and, on a phone's small picture, runs into the
  // lines and the vehicles' labels; the outline says where it is, and the Setup page has the names.
  if (options.zones) drawZones(ctx, size, scene.zones, options.labels && !compact, colors, compact);
  drawStopSigns(ctx, size, scene.stopSigns ?? [], options, colors, compact);

  if (options.trails) {
    for (const [, trail] of trails) {
      if (trail.points.length < 2) continue;
      ctx.save();
      ctx.strokeStyle = colors.forClass(trail.label);
      ctx.lineWidth = 2;
      ctx.globalAlpha = 0.7;
      ctx.beginPath();
      trail.points.forEach(({ p }, index) => {
        const x = p[0] * size.width;
        const y = p[1] * size.height;
        if (index === 0) ctx.moveTo(x, y);
        else ctx.lineTo(x, y);
      });
      ctx.stroke();
      ctx.restore();
    }
  }

  if (options.lines) {
    for (const line of scene.lines) {
      const arrow = directionArrow(line, size);
      const ax = line.a[0] * size.width;
      const ay = line.a[1] * size.height;
      const bx = line.b[0] * size.width;
      const by = line.b[1] * size.height;
      // A dark halo under a white line keeps it readable on any part of the picture.
      for (const [style, width] of [[colors.chipEdge, 5], [colors.line, 2.5]] as const) {
        ctx.strokeStyle = style;
        ctx.lineWidth = width;
        ctx.beginPath();
        ctx.moveTo(ax, ay);
        ctx.lineTo(bx, by);
        ctx.moveTo(arrow.from[0], arrow.from[1]);
        ctx.lineTo(arrow.to[0], arrow.to[1]);
        for (const wing of arrowHead(arrow.from, arrow.to)) {
          ctx.moveTo(arrow.to[0], arrow.to[1]);
          ctx.lineTo(wing[0], wing[1]);
        }
        ctx.stroke();
      }
      if (options.labels && line.name) drawChip(ctx, line.name, ax + 2, Math.max(16, ay - 4), colors.line, compact);
    }
  }

  if (options.boxes) {
    for (const track of scene.tracks) {
      const rect = boxRect(track.box, size);
      const color = colors.forClass(track.label);
      ctx.save();
      ctx.strokeStyle = color;
      ctx.lineWidth = track.confirmed ? 2 : 1;
      ctx.globalAlpha = track.confirmed ? 1 : 0.55;
      ctx.strokeRect(rect.x, rect.y, rect.w, rect.h);
      ctx.restore();
      if (options.labels && track.confirmed && rect.w >= MIN_LABELLED_PX) {
        drawChip(ctx, trackLabel(track, compact, scene.units), rect.x, Math.max(14, rect.y), color, compact);
      }
    }
  }
}

/** A small filled label sitting on top of (x, y), text black or white by the fill's brightness. */
function drawChip(ctx: Canvas2D, text: string, x: number, y: number, fill: string, compact = false): void {
  ctx.save();
  ctx.font = compact ? COMPACT_FONT : LABEL_FONT;
  ctx.textBaseline = "alphabetic";
  const height = compact ? 13 : 15;
  const width = ctx.measureText(text).width + 8;
  ctx.fillStyle = fill;
  ctx.fillRect(x, y - height + 1, width, height);
  ctx.fillStyle = chipTextColor(fill);
  ctx.fillText(text, x + 4, y - 3);
  ctx.restore();
}

/** Concrete colours from the page's CSS variables, for the current light or dark theme. */
export function readOverlayColors(root: HTMLElement = document.documentElement): OverlayColors {
  const style = getComputedStyle(root);
  const read = (name: string, fallback: string) => style.getPropertyValue(name).trim() || fallback;
  const cache = new Map<string, string>();
  return {
    forClass: (name) => {
      let value = cache.get(name);
      if (value === undefined) {
        // classColor() gives "var(--series-N)"; read the variable behind it.
        const variable = /var\((--[\w-]+)\)/.exec(classColor(name))?.[1] ?? "--series-other";
        value = read(variable, "#898781");
        cache.set(name, value);
      }
      return value;
    },
    line: "#ffffff",
    chipEdge: "#0b0b0b",
    exclusionFill: "rgba(11, 11, 11, 0.42)",
    stopLine: "#d63030",
    stopFill: "rgba(214, 48, 48, 0.16)",
  };
}
