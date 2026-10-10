import { describe, expect, it, vi } from "vitest";
import type { OverlayColors } from "./overlay";
import {
  BUSIEST_OPACITY,
  HEAT_COLUMNS,
  HEAT_FLOOR,
  HEAT_STOPS,
  MAX_PATH_ALPHA,
  MAX_PATH_WIDTH,
  MIN_PATH_ALPHA,
  MIN_PATH_WIDTH,
  blurGrid,
  drawHeat,
  drawPaths,
  drawTrailScene,
  heatColor,
  heatGradientCss,
  heatGrid,
  heatPixels,
  heatReference,
  heatSize,
  mixedOrder,
  pathAlpha,
  pathWidth,
  type HeatGrid,
  type PaintGrid,
  type TrailCanvas,
  type TrailScene,
} from "./trails-draw";
import type { TrailPath } from "./types";

const SCALE = 65535;
const size = { width: 640, height: 360 };
const colors: OverlayColors = {
  forClass: (name) => ({ car: "#2a78d6", truck: "#eb6834" })[name] ?? "#898781",
  line: "#ffffff",
  chipEdge: "#0b0b0b",
  exclusionFill: "rgba(0, 0, 0, 0.4)",
  stopLine: "#d63030",
  stopFill: "rgba(214, 48, 48, 0.16)",
};

let nextId = 1;
/** A path through points given as fractions of the picture. */
function path(label: string, ...points: [number, number][]): TrailPath {
  return {
    id: nextId++,
    started_at: 0,
    ended_at: 1,
    label,
    x: points.map(([x]) => Math.round(x * SCALE)),
    y: points.map(([, y]) => Math.round(y * SCALE)),
  };
}

interface Op {
  name: string;
  args: unknown[];
  style?: unknown;
  alpha?: number;
  width?: number;
}

/** A canvas that writes down what it is asked to do, with the style in force at each stroke. */
function recorder() {
  const ops: Op[] = [];
  const ctx = {
    strokeStyle: "",
    fillStyle: "",
    lineWidth: 1,
    globalAlpha: 1,
    font: "",
    lineJoin: "miter",
    lineCap: "butt",
    textBaseline: "alphabetic",
    imageSmoothingEnabled: false,
    imageSmoothingQuality: "low",
  } as Record<string, unknown>;
  for (const name of ["save", "restore", "clearRect", "beginPath", "moveTo", "lineTo", "stroke", "fill", "closePath", "fillRect", "strokeRect", "fillText", "strokeText", "setLineDash", "drawImage"]) {
    ctx[name] = (...args: unknown[]) => {
      ops.push({ name, args, style: ctx.strokeStyle, alpha: ctx.globalAlpha as number, width: ctx.lineWidth as number });
    };
  }
  ctx.measureText = () => ({ width: 40 });
  return { ctx: ctx as unknown as TrailCanvas, ops };
}

const names = (ops: Op[]) => ops.map((op) => op.name);

describe("how the paths are drawn", () => {
  it("draws a lone path firmly, and each path fainter where many pile up, within limits", () => {
    expect(pathAlpha(1)).toBe(MAX_PATH_ALPHA);
    expect(pathAlpha(0)).toBe(MAX_PATH_ALPHA);
    const alphas = [1, 2, 5, 10, 30, 100, 1000].map(pathAlpha);
    expect([...alphas].sort((a, b) => b - a)).toEqual(alphas);
    expect(new Set(alphas).size).toBeGreaterThan(5);
    expect(pathAlpha(100_000)).toBe(MIN_PATH_ALPHA);
    // the paths piled up where it is busiest add up to about the opacity aimed for
    for (const stacked of [2, 5, 10, 30]) expect(1 - (1 - pathAlpha(stacked)) ** stacked).toBeCloseTo(BUSIEST_OPACITY, 2);
  });

  it("makes each path thicker on a wider picture and thinner as there are more of them, within limits", () => {
    expect(pathWidth(10, 900)).toBe(3);
    expect(pathWidth(100, 900)).toBeCloseTo(2.4);
    expect(pathWidth(1000, 900)).toBeCloseTo(1.95);
    expect(pathWidth(10, 360)).toBe(MIN_PATH_WIDTH); // 1.2 would be a hair on a phone
    expect(pathWidth(10, 5000)).toBe(MAX_PATH_WIDTH);
    expect(pathWidth(10, 0)).toBe(MIN_PATH_WIDTH);
  });

  it("strokes every path once, scaled from the stored numbers to pixels", () => {
    const { ctx, ops } = recorder();
    drawPaths(ctx, size, [path("car", [0, 0.5], [0.5, 0.5], [1, 1])], SCALE, colors);
    expect(names(ops)).toEqual(["save", "beginPath", "moveTo", "lineTo", "lineTo", "stroke", "restore"]);
    // the stored numbers are whole, so a half is a hair over (32768 of 65535)
    const near = (args: unknown[] | undefined, expected: number[]) => expected.forEach((value, at) => expect(args?.[at] as number).toBeCloseTo(value, 1));
    near(ops.find((op) => op.name === "moveTo")?.args, [0, 180]);
    const ends = ops.filter((op) => op.name === "lineTo").map((op) => op.args);
    near(ends[0], [320, 180]);
    near(ends[1], [640, 360]);
    expect(ends).toHaveLength(2);
  });

  it("gives each path a stroke of its own in its kind's colour, faint enough for the busiest place", () => {
    const { ctx, ops } = recorder();
    // three paths that cross one another at the middle
    const paths = [path("truck", [0, 0], [1, 1]), path("car", [0, 1], [1, 0]), path("car", [0, 0.5], [1, 0.5])];
    drawPaths(ctx, size, paths, SCALE, colors);
    const strokes = ops.filter((op) => op.name === "stroke");
    expect(strokes).toHaveLength(3);
    expect(strokes.map((op) => op.style).sort()).toEqual(["#2a78d6", "#2a78d6", "#eb6834"]);
    const overlap = heatReference(heatGrid(paths, SCALE, HEAT_COLUMNS, heatSize(size.width / size.height).height).cells);
    expect(strokes.every((op) => op.alpha === pathAlpha(overlap) && op.width === pathWidth(3, size.width))).toBe(true);
  });

  it("makes each path fainter the more of them there are on the same road", () => {
    const alphaOf = (count: number) => {
      const { ctx, ops } = recorder();
      const lane = Array.from({ length: count }, () => path("car", [0, 0.5], [1, 0.5]));
      drawPaths(ctx, size, lane, SCALE, colors);
      return ops.find((op) => op.name === "stroke")?.alpha as number;
    };
    expect(alphaOf(1)).toBe(MAX_PATH_ALPHA);
    expect(alphaOf(10)).toBeLessThan(alphaOf(2));
    expect(alphaOf(100)).toBeLessThan(alphaOf(10));
    // and paths that do not meet stay firm, however many there are
    const { ctx, ops } = recorder();
    drawPaths(ctx, size, [path("car", [0, 0.2], [1, 0.2]), path("car", [0, 0.8], [1, 0.8])], SCALE, colors);
    expect(ops.find((op) => op.name === "stroke")?.alpha).toBe(MAX_PATH_ALPHA);
  });

  it("draws the paths in an order that has nothing to do with their kind", () => {
    const paths = Array.from({ length: 60 }, (_, index) => path(index < 30 ? "car" : "bicycle", [0, 0.2 + index * 0.01], [1, 0.2 + index * 0.01]));
    const { ctx, ops } = recorder();
    drawPaths(ctx, size, paths, SCALE, colors);
    const styles = ops.filter((op) => op.name === "stroke").map((op) => op.style);
    // not all the cars and then all the bicycles: the colours alternate through the drawing
    const changes = styles.filter((style, index) => index > 0 && style !== styles[index - 1]).length;
    expect(changes).toBeGreaterThan(10);
  });

  it("puts a kind it does not know in the grey", () => {
    const { ctx, ops } = recorder();
    drawPaths(ctx, size, [path("scooter", [0, 0], [1, 1])], SCALE, colors);
    expect(ops.find((op) => op.name === "stroke")?.style).toBe("#898781");
  });

  it("leaves the canvas as it found it", () => {
    const { ctx, ops } = recorder();
    drawPaths(ctx, size, [path("car", [0, 0], [1, 1])], SCALE, colors);
    expect(names(ops).filter((n) => n === "save")).toHaveLength(names(ops).filter((n) => n === "restore").length);
  });

  it("skips a path of one point, draws a ragged one as far as both lists go, and does nothing for nothing", () => {
    const { ctx, ops } = recorder();
    const ragged: TrailPath = { ...path("car", [0, 0], [0.5, 0.5], [1, 1]), y: [0, 100] };
    drawPaths(ctx, size, [path("car", [0.5, 0.5]), ragged], SCALE, colors);
    expect(ops.filter((op) => op.name === "stroke")).toHaveLength(1);
    expect(ops.filter((op) => op.name === "lineTo")).toHaveLength(1);
    const again = recorder();
    drawPaths(again.ctx, size, [], SCALE, colors);
    drawPaths(again.ctx, size, [path("car", [0, 0], [1, 1])], 0, colors);
    drawPaths(again.ctx, { width: 0, height: 0 }, [path("car", [0, 0], [1, 1])], SCALE, colors);
    expect(again.ops).toEqual([]);
  });
});

describe("the order paths are drawn in", () => {
  it("is the same every time and keeps every path", () => {
    const paths = Array.from({ length: 50 }, (_, index) => ({ id: index + 1 }));
    const first = mixedOrder(paths);
    expect(mixedOrder(paths)).toEqual(first);
    expect(first.map((p) => p.id).sort((a, b) => a - b)).toEqual(paths.map((p) => p.id));
    expect(first.map((p) => p.id)).not.toEqual(paths.map((p) => p.id)); // and is not the order given
    expect(paths.map((p) => p.id)).toEqual(Array.from({ length: 50 }, (_, index) => index + 1)); // which is not changed
  });

  it("does not put the first half of the list in the first half", () => {
    const paths = Array.from({ length: 200 }, (_, index) => ({ id: index + 1 }));
    const firstHalf = mixedOrder(paths).slice(0, 100);
    const fromEarly = firstHalf.filter((p) => p.id <= 100).length;
    expect(fromEarly).toBeGreaterThan(30);
    expect(fromEarly).toBeLessThan(70);
  });
});

describe("the heat grid", () => {
  const cell = (grid: HeatGrid, column: number, row: number) => grid.cells[row * grid.width + column];

  it("counts a path once in each cell it passes through, however long it stays", () => {
    // across the middle of a 10 x 10 grid, with three points inside the first cell
    const along = path("car", [0.01, 0.55], [0.02, 0.55], [0.03, 0.55], [0.95, 0.55]);
    const grid = heatGrid([along], SCALE, 10, 10);
    for (let column = 0; column < 10; column++) expect(cell(grid, column, 5)).toBe(1);
    expect(grid.cells.reduce((a, b) => a + b, 0)).toBe(10);
  });

  it("adds up the paths that share a cell", () => {
    const one = path("car", [0.05, 0.55], [0.95, 0.55]);
    const two = path("truck", [0.05, 0.55], [0.55, 0.55]);
    const grid = heatGrid([one, two], SCALE, 10, 10);
    expect(cell(grid, 0, 5)).toBe(2);
    expect(cell(grid, 5, 5)).toBe(2);
    expect(cell(grid, 6, 5)).toBe(1);
  });

  it("leaves no gap where a vehicle jumped several cells between two points", () => {
    const grid = heatGrid([path("car", [0.05, 0.05], [0.95, 0.95])], SCALE, 10, 10);
    for (let step = 0; step < 10; step++) expect(cell(grid, step, step)).toBe(1);
  });

  it("keeps a point outside the picture inside the grid, and copes with no scale", () => {
    const outside: TrailPath = { id: 1, started_at: 0, ended_at: 1, label: "car", x: [-5, 70_000], y: [-5, 70_000] };
    const grid = heatGrid([outside], SCALE, 4, 4);
    expect(grid.cells.reduce((a, b) => a + b, 0)).toBeGreaterThan(0);
    expect(heatGrid([outside], 0, 4, 4).cells.every((n) => n === 0)).toBe(true);
  });

  it("is as wide as the picture and as tall as keeps the cells square", () => {
    expect(heatSize(16 / 9)).toEqual({ width: HEAT_COLUMNS, height: 108 });
    expect(heatSize(4 / 3)).toEqual({ width: HEAT_COLUMNS, height: 144 });
    expect(heatSize(0).height).toBe(108); // no shape known: the usual one
  });
});

describe("blurring the heat grid", () => {
  const spike = (): HeatGrid => {
    const cells = new Float32Array(81);
    cells[4 * 9 + 4] = 9;
    return { width: 9, height: 9, cells };
  };

  it("spreads a spike to its neighbours without changing the total", () => {
    const blurred = blurGrid(spike());
    expect(blurred.cells[4 * 9 + 4]).toBeLessThan(9);
    expect(blurred.cells[4 * 9 + 5]).toBeGreaterThan(0);
    expect(blurred.cells[4 * 9 + 7]).toBe(0); // three cells away is out of reach of two passes
    expect(blurred.cells.reduce((a, b) => a + b, 0)).toBeCloseTo(9, 4);
  });

  it("does not change the grid it is given", () => {
    const grid = spike();
    blurGrid(grid);
    expect(grid.cells[4 * 9 + 4]).toBe(9);
    expect(blurGrid(grid, 0).cells).toEqual(grid.cells);
  });
});

describe("the heat colours", () => {
  it("are clear for nothing and pale yellow for the busiest spots", () => {
    expect(heatColor(0)[3]).toBe(0);
    expect(heatColor(1)).toEqual([252, 255, 164, 242]);
  });

  it("get more opaque and brighter as they get hotter", () => {
    const levels = [0, 0.05, 0.1, 0.25, 0.4, 0.6, 0.8, 0.95, 1];
    const alphas = levels.map((level) => heatColor(level)[3]);
    expect([...alphas].sort((a, b) => a - b)).toEqual(alphas);
    expect(new Set(alphas).size).toBe(alphas.length);
  });

  it("blend between the stops", () => {
    const [r, g, b, a] = heatColor(0.35); // half way from the stop at 0.25 to the one at 0.45
    expect([r, g, b]).toEqual([130, 32, 105]);
    expect(a).toBe(Math.round(0.625 * 255));
  });

  it("make sense of a level out of range or not a number", () => {
    expect(heatColor(-3)).toEqual(heatColor(0));
    expect(heatColor(7)).toEqual(heatColor(1));
    expect(heatColor(Number.NaN)).toEqual(heatColor(0));
  });

  it("are the same in the legend", () => {
    const css = heatGradientCss();
    expect(css.startsWith("linear-gradient(to right, ")).toBe(true);
    expect(css.match(/rgb\(/g)).toHaveLength(HEAT_STOPS.length);
    expect(css).toContain("rgb(252 255 164) 100%");
  });
});

describe("the heat pixels", () => {
  it("leave empty cells clear, and those under the floor, and make the busiest spot the hottest", () => {
    const cells = new Float32Array(16);
    cells[0] = 1;
    cells[5] = 0.01; // far below a hundredth of the busiest
    cells[10] = 40;
    cells[11] = 20;
    // the busiest of a handful of cells is also the one the others are measured against
    const pixels = heatPixels({ width: 4, height: 4, cells });
    expect(pixels).toHaveLength(64);
    const alpha = (cell: number) => pixels[cell * 4 + 3]!;
    expect(alpha(1)).toBe(0);
    expect(alpha(5)).toBe(0);
    expect(alpha(10)).toBeGreaterThan(alpha(11)!);
    expect(alpha(11)).toBeGreaterThan(alpha(0));
    expect(alpha(10)).toBeGreaterThan(200);
  });

  it("measure against nearly the busiest spot, and at least one vehicle", () => {
    expect(heatReference(new Float32Array(10))).toBe(1);
    expect(heatReference(new Float32Array([0.3, 0.2]))).toBe(1);
    const many = new Float32Array(200).fill(2);
    many[0] = 500; // one outlier does not set the scale
    expect(heatReference(many)).toBe(2);
    expect(HEAT_FLOOR).toBeGreaterThan(0);
  });
});

describe("drawing the heat map", () => {
  const paint: PaintGrid = vi.fn(() => "image" as unknown as CanvasImageSource);

  it("makes the grid in the shape of the picture and draws it over all of it, smoothed", () => {
    const { ctx, ops } = recorder();
    drawHeat(ctx, size, [path("car", [0, 0.5], [1, 0.5])], SCALE, paint);
    expect(paint).toHaveBeenCalledWith(expect.any(Uint8ClampedArray), HEAT_COLUMNS, 108);
    const draw = ops.find((op) => op.name === "drawImage");
    expect(draw?.args).toEqual(["image", 0, 0, 640, 360]);
    expect(names(ops)).toEqual(["save", "drawImage", "restore"]);
  });

  it("draws nothing for nothing", () => {
    const { ctx, ops } = recorder();
    drawHeat(ctx, size, [], SCALE, paint);
    drawHeat(ctx, { width: 0, height: 0 }, [path("car", [0, 0], [1, 1])], SCALE, paint);
    expect(ops).toEqual([]);
  });
});

describe("the whole picture", () => {
  const scene = (over: Partial<TrailScene> = {}): TrailScene => ({
    paths: [path("car", [0, 0.2], [1, 0.2]), path("car", [0, 0.8], [1, 0.8])],
    scale: SCALE,
    view: "paths",
    lines: [{ id: "n", name: "North", a: [0.2, 0.5], b: [0.8, 0.5] }],
    zones: [],
    stopSigns: [],
    showLinesAndZones: true,
    ...over,
  });
  const paint: PaintGrid = () => "image" as unknown as CanvasImageSource;

  it("clears once, draws the paths, then the lines over them", () => {
    const { ctx, ops } = recorder();
    drawTrailScene(ctx, size, scene(), colors, paint);
    expect(names(ops).filter((n) => n === "clearRect")).toHaveLength(1);
    expect(names(ops)[0]).toBe("clearRect");
    const strokes = ops.filter((op) => op.name === "stroke");
    expect(strokes.length).toBeGreaterThan(2);
    expect(strokes[0]?.style).toBe("#2a78d6"); // a path
    expect(strokes.at(-1)?.style).toBe("#ffffff"); // the line, last, so on top
    expect(ops.some((op) => op.name === "drawImage")).toBe(false);
  });

  it("draws the heat map instead of the paths when asked", () => {
    const { ctx, ops } = recorder();
    drawTrailScene(ctx, size, scene({ view: "heat" }), colors, paint);
    expect(ops.some((op) => op.name === "drawImage")).toBe(true);
    expect(ops.filter((op) => op.style === "#2a78d6")).toHaveLength(0);
  });

  it("draws a stop sign over the paths with the lines and zones, and not when they are off", () => {
    const sign = { id: "elm", name: "Elm", approach: [[0.1, 0.5], [0.5, 0.5], [0.5, 0.9], [0.1, 0.9]] as [number, number][], a: [0.5, 0.9] as [number, number], b: [0.5, 0.5] as [number, number], direction: 1 as const };
    const withSign = recorder();
    drawTrailScene(withSign.ctx, size, scene({ lines: [], stopSigns: [sign] }), colors, paint);
    expect(withSign.ops.filter((op) => op.name === "stroke").at(-1)?.style).toBe("#ffffff"); // the arrow, over the red line, over the paths
    expect(withSign.ops.some((op) => op.style === "#d63030")).toBe(true);
    const without = recorder();
    drawTrailScene(without.ctx, size, scene({ lines: [], stopSigns: [sign], showLinesAndZones: false }), colors, paint);
    expect(without.ops.some((op) => op.style === "#d63030")).toBe(false);
  });

  it("leaves the lines and zones out when they are switched off", () => {
    const { ctx, ops } = recorder();
    drawTrailScene(ctx, size, scene({ showLinesAndZones: false }), colors, paint);
    expect(ops.filter((op) => op.name === "stroke")).toHaveLength(2);
  });
});
