import { paintOverlay, type Canvas2D, type OverlayColors, type Size } from "./overlay";
import type { TrailView } from "./trails";
import type { LiveLine, LiveZone, TrailPath } from "./types";

/** What drawing the Trails picture needs of a canvas: the overlay's calls, and a picture to scale up. */
export type TrailCanvas = Canvas2D & Pick<CanvasRenderingContext2D, "drawImage" | "imageSmoothingEnabled" | "imageSmoothingQuality">;

/**
 * Turns a grid of colours into something that can be drawn: the page makes a small canvas of it. The
 * drawing code does not know how, so that it can be tested without a browser.
 */
export type PaintGrid = (pixels: Uint8ClampedArray<ArrayBuffer>, width: number, height: number) => CanvasImageSource;

export interface TrailScene {
  /** Only the paths to draw (the kinds switched off are already left out). */
  paths: readonly TrailPath[];
  /** Divide a path's numbers by this to get a fraction of the picture. */
  scale: number;
  view: TrailView;
  lines: readonly LiveLine[];
  zones: readonly LiveZone[];
  showLinesAndZones: boolean;
}

/** Everything of the Trails picture that is drawn: the paths or the heat map, then the lines and zones on top. */
export function drawTrailScene(ctx: TrailCanvas, size: Size, scene: TrailScene, colors: OverlayColors, paint: PaintGrid): void {
  ctx.clearRect(0, 0, size.width, size.height);
  if (scene.view === "heat") drawHeat(ctx, size, scene.paths, scene.scale, paint);
  else drawPaths(ctx, size, scene.paths, scene.scale, colors);
  if (scene.showLinesAndZones) {
    const options = { boxes: false, labels: true, trails: false, lines: true, zones: true };
    paintOverlay(ctx, size, { lines: scene.lines, zones: scene.zones, tracks: [] }, options, new Map(), colors);
  }
}

// --- paths ------------------------------------------------------------------------------------------

export const MAX_PATH_ALPHA = 0.85;
export const MIN_PATH_ALPHA = 0.03;
/** How opaque the busiest places should come out when every path that passes over them is drawn. */
export const BUSIEST_OPACITY = 0.85;

/**
 * How opaque each path is, given how many of them pass over the busiest places. A path alone is
 * drawn firmly; where many pile up each is faint, so that together they reach about the same
 * opacity instead of a solid smear. (A canvas keeps 8 bits of opacity, so there is a floor.)
 */
export function pathAlpha(overlap: number): number {
  const stacked = Math.max(1, overlap);
  const each = 1 - (1 - BUSIEST_OPACITY) ** (1 / stacked);
  return Math.min(MAX_PATH_ALPHA, Math.max(MIN_PATH_ALPHA, each));
}

/** The thickest and thinnest a path is drawn, in pixels. */
export const MAX_PATH_WIDTH = 5;
export const MIN_PATH_WIDTH = 1.5;

/**
 * How thick, in pixels: in proportion to the picture (a path that is thin on a wide screen is
 * invisible, one that is thick on a phone is a smear), and thinner as there get to be more of them.
 */
export function pathWidth(count: number, pictureWidth: number): number {
  const crowding = count <= 25 ? 1 : count <= 400 ? 0.8 : 0.65;
  return Math.min(MAX_PATH_WIDTH, Math.max(MIN_PATH_WIDTH, (pictureWidth / 300) * crowding));
}

/**
 * The paths in an order that has nothing to do with their kind or their time, the same every time.
 * Where they pile up, the last drawn weighs most, so drawing the cars first and the bicycles last
 * would paint every busy lane in the bicycles' colour.
 */
export function mixedOrder<T extends { id: number }>(paths: readonly T[]): T[] {
  const key = (path: T) => Math.imul(path.id, 2654435761) >>> 0;
  return [...paths].sort((a, b) => key(a) - key(b) || a.id - b.id);
}

/** Every path as a thin translucent line in its kind's colour (one stroke each, so that overlaps build up). */
export function drawPaths(ctx: Canvas2D, size: Size, paths: readonly TrailPath[], scale: number, colors: Pick<OverlayColors, "forClass">): void {
  if (paths.length === 0 || scale <= 0 || size.width <= 0 || size.height <= 0) return;
  const kx = size.width / scale;
  const ky = size.height / scale;
  const { height: rows } = heatSize(size.width / size.height);
  const overlap = heatReference(heatGrid(paths, scale, HEAT_COLUMNS, rows).cells);
  ctx.save();
  ctx.lineJoin = "round";
  ctx.lineCap = "round";
  ctx.lineWidth = pathWidth(paths.length, size.width);
  ctx.globalAlpha = pathAlpha(overlap);
  for (const path of mixedOrder(paths)) {
    const points = Math.min(path.x.length, path.y.length);
    if (points < 2) continue;
    ctx.strokeStyle = colors.forClass(path.label);
    ctx.beginPath();
    ctx.moveTo(path.x[0]! * kx, path.y[0]! * ky);
    for (let index = 1; index < points; index++) ctx.lineTo(path.x[index]! * kx, path.y[index]! * ky);
    ctx.stroke();
  }
  ctx.restore();
}

// --- the heat map -----------------------------------------------------------------------------------

export const HEAT_COLUMNS = 192;

export interface HeatGrid {
  width: number;
  height: number;
  cells: Float32Array;
}

/** The grid for a picture of this shape: a fixed number of columns, and as many rows as keep the cells square. */
export function heatSize(aspect: number, columns = HEAT_COLUMNS): { width: number; height: number } {
  return { width: columns, height: Math.max(1, Math.round(columns / (aspect > 0 ? aspect : 16 / 9))) };
}

/**
 * How many paths went through each cell. A path counts once for a cell however long it stayed in
 * it, so the map says how many vehicles passed, not how long they took.
 */
export function heatGrid(paths: readonly TrailPath[], scale: number, width: number, height: number): HeatGrid {
  const cells = new Float32Array(width * height);
  if (scale <= 0) return { width, height, cells };
  const lastVisit = new Int32Array(width * height); // which path counted a cell last
  const kx = width / scale;
  const ky = height / scale;
  paths.forEach((path, order) => {
    const mark = order + 1;
    const visit = (x: number, y: number) => {
      const column = Math.min(width - 1, Math.max(0, Math.floor(x)));
      const row = Math.min(height - 1, Math.max(0, Math.floor(y)));
      const at = row * width + column;
      if (lastVisit[at] !== mark) {
        lastVisit[at] = mark;
        cells[at] = (cells[at] ?? 0) + 1;
      }
    };
    const points = Math.min(path.x.length, path.y.length);
    let previousX = 0;
    let previousY = 0;
    for (let index = 0; index < points; index++) {
      const x = path.x[index]! * kx;
      const y = path.y[index]! * ky;
      if (index > 0) {
        // Walk the segment in half-cell steps, so that a fast vehicle leaves no gaps.
        const steps = Math.max(1, Math.ceil(Math.max(Math.abs(x - previousX), Math.abs(y - previousY)) * 2));
        for (let step = 1; step < steps; step++) visit(previousX + ((x - previousX) * step) / steps, previousY + ((y - previousY) * step) / steps);
      }
      visit(x, y);
      previousX = x;
      previousY = y;
    }
  });
  return { width, height, cells };
}

/** Softens the grid so that cells blend into a surface (two passes of a three-cell average each way). */
export function blurGrid(grid: HeatGrid, passes = 2): HeatGrid {
  const { width, height } = grid;
  let from = new Float32Array(grid.cells);
  let to = new Float32Array(from.length);
  for (let pass = 0; pass < passes; pass++) {
    for (let row = 0; row < height; row++) {
      const base = row * width;
      for (let column = 0; column < width; column++) {
        to[base + column] = (from[base + Math.max(0, column - 1)]! + from[base + column]! + from[base + Math.min(width - 1, column + 1)]!) / 3;
      }
    }
    [from, to] = [to, from];
    for (let row = 0; row < height; row++) {
      for (let column = 0; column < width; column++) {
        to[row * width + column] = (from[Math.max(0, row - 1) * width + column]! + from[row * width + column]! + from[Math.min(height - 1, row + 1) * width + column]!) / 3;
      }
    }
    [from, to] = [to, from];
  }
  return { width, height, cells: from };
}

/** The density that is drawn as the hottest colour: nearly the highest, so that one outlier does not wash out the rest. */
export function heatReference(cells: Float32Array): number {
  const filled: number[] = [];
  for (const value of cells) if (value > 0.05) filled.push(value);
  if (filled.length === 0) return 1;
  filled.sort((a, b) => a - b);
  return Math.max(1, filled[Math.min(filled.length - 1, Math.floor(filled.length * 0.98))]!);
}

interface HeatStop {
  at: number;
  rgb: readonly [number, number, number];
  alpha: number;
}

/** Dark purple through red and orange to pale yellow, getting more opaque as it gets hotter. */
export const HEAT_STOPS: readonly HeatStop[] = [
  { at: 0, rgb: [0, 0, 4], alpha: 0 },
  { at: 0.05, rgb: [40, 11, 84], alpha: 0.3 },
  { at: 0.25, rgb: [101, 21, 110], alpha: 0.55 },
  { at: 0.45, rgb: [159, 42, 99], alpha: 0.7 },
  { at: 0.65, rgb: [212, 72, 66], alpha: 0.8 },
  { at: 0.85, rgb: [245, 125, 21], alpha: 0.88 },
  { at: 1, rgb: [252, 255, 164], alpha: 0.95 },
];

/** The colour for a level from 0 (nothing) to 1 (the busiest spots): red, green, blue and alpha, each 0 to 255. */
export function heatColor(level: number): [number, number, number, number] {
  const at = Math.min(1, Math.max(0, Number.isFinite(level) ? level : 0));
  const upper = HEAT_STOPS.findIndex((stop) => stop.at >= at);
  const high = HEAT_STOPS[upper === -1 ? HEAT_STOPS.length - 1 : upper]!;
  const low = HEAT_STOPS[Math.max(0, upper - 1)]!;
  const span = high.at - low.at;
  const mix = span === 0 ? 1 : (at - low.at) / span;
  const blend = (a: number, b: number) => a + (b - a) * mix;
  return [
    Math.round(blend(low.rgb[0], high.rgb[0])),
    Math.round(blend(low.rgb[1], high.rgb[1])),
    Math.round(blend(low.rgb[2], high.rgb[2])),
    Math.round(blend(low.alpha, high.alpha) * 255),
  ];
}

/** A cell with less than this share of the busiest spots' density is left clear, so that blurring leaves no haze. */
export const HEAT_FLOOR = 0.06;
/** Below 1 lifts the quiet roads, so that they show next to the busiest one. */
export const HEAT_GAMMA = 0.7;

/** The grid as pixels (red, green, blue, alpha for each cell), hotter cells more opaque. */
export function heatPixels(grid: HeatGrid): Uint8ClampedArray<ArrayBuffer> {
  const reference = heatReference(grid.cells);
  const pixels = new Uint8ClampedArray(grid.cells.length * 4);
  grid.cells.forEach((value, index) => {
    const share = Math.min(1, value / reference);
    const [r, g, b, a] = share < HEAT_FLOOR ? [0, 0, 0, 0] : heatColor(share ** HEAT_GAMMA);
    pixels.set([r, g, b, a], index * 4);
  });
  return pixels;
}

/** The ramp as a CSS gradient for the legend, from "fewer" to "more". */
export function heatGradientCss(): string {
  const stops = HEAT_STOPS.map((stop) => `rgb(${stop.rgb.join(" ")}) ${Math.round(stop.at * 100)}%`);
  return `linear-gradient(to right, ${stops.join(", ")})`;
}

/** The heat map of all the paths, scaled up smoothly over the whole picture. */
export function drawHeat(ctx: TrailCanvas, size: Size, paths: readonly TrailPath[], scale: number, paint: PaintGrid): void {
  if (paths.length === 0 || scale <= 0 || size.width <= 0 || size.height <= 0) return;
  const { width, height } = heatSize(size.width / size.height);
  const grid = blurGrid(heatGrid(paths, scale, width, height));
  const image = paint(heatPixels(grid), width, height);
  ctx.save();
  ctx.imageSmoothingEnabled = true;
  ctx.imageSmoothingQuality = "high";
  ctx.drawImage(image, 0, 0, size.width, size.height);
  ctx.restore();
}
