import { describe, expect, it, vi } from "vitest";
import {
  type Canvas2D,
  type OverlayColors,
  arrowHead,
  boxRect,
  chipTextColor,
  trackLabel,
  zoneLabel,
  directionArrow,
  drawOverlay,
  groundPoint,
  updateTrails,
  type Trail,
} from "./overlay";
import type { LiveTrack, LiveZone } from "./types";

const size = { width: 640, height: 360 };

function track(id: number, over: Partial<LiveTrack> = {}): LiveTrack {
  return { id, label: "car", score: 0.9, box: [0.1, 0.2, 0.3, 0.6], velocity: [0, 0], confirmed: true, ...over };
}

describe("geometry", () => {
  it("scales a box of fractions to pixels", () => {
    const rect = boxRect([0.1, 0.2, 0.3, 0.6], size);
    expect([rect.x, rect.y, rect.w, rect.h]).toEqual([64, 72, expect.closeTo(128), expect.closeTo(144)]);
  });

  it("puts a vehicle's ground point at the middle of its bottom edge", () => {
    expect(groundPoint([0.1, 0.2, 0.3, 0.6])).toEqual([0.2, 0.6]);
  });

  it("points the arrow to the right-hand side of the line's direction", () => {
    // a -> b runs left to right across the middle; with y pointing down, its right-hand side is down.
    const { from, to } = directionArrow({ a: [0.2, 0.5], b: [0.8, 0.5] }, size, 26);
    expect(from).toEqual([320, 180]);
    expect(to[0]).toBeCloseTo(320);
    expect(to[1]).toBeCloseTo(206);
    // Reversing the line reverses the arrow.
    const back = directionArrow({ a: [0.8, 0.5], b: [0.2, 0.5] }, size, 26);
    expect(back.to[1]).toBeCloseTo(154);
  });

  it("copes with a line of no length", () => {
    const { from, to } = directionArrow({ a: [0.5, 0.5], b: [0.5, 0.5] }, size);
    expect(Number.isFinite(to[0]) && Number.isFinite(to[1])).toBe(true);
    expect(from).toEqual([320, 180]);
  });
});

describe("arrowHead", () => {
  it("makes two strokes that trail back from the tip on either side of the shaft", () => {
    const [left, right] = arrowHead([0, 0], [0, 20], 8, Math.PI / 4); // pointing down the screen
    expect(left[1]).toBeCloseTo(20 - 8 * Math.cos(Math.PI / 4));
    expect(right[1]).toBeCloseTo(left[1]);
    expect(left[0]).toBeCloseTo(-right[0]); // mirror images
    expect(Math.abs(left[0])).toBeGreaterThan(1);
  });

  it("copes with an arrow of no length", () => {
    const wings = arrowHead([5, 5], [5, 5]);
    expect(wings.flat().every(Number.isFinite)).toBe(true);
  });
});

describe("chip text colour", () => {
  it("is white on dark fills and black on light ones", () => {
    expect(chipTextColor("#1a1a19")).toBe("#ffffff");
    expect(chipTextColor("#256abf")).toBe("#ffffff");
    expect(chipTextColor("#eda100")).toBe("#000000"); // yellow
    expect(chipTextColor("#ffffff")).toBe("#000000");
    expect(chipTextColor("not a colour")).toBe("#ffffff");
  });
});

describe("labels", () => {
  it("say the id, the kind and the score when there is room, and just the kind when there is not", () => {
    const t = { id: 12, label: "truck", score: 0.876 };
    expect(trackLabel(t, false)).toBe("#12 truck 88%");
    expect(trackLabel(t, true)).toBe("truck");
  });

  it("are short on a small picture and left out for a box too small to carry one", () => {
    const { ctx } = recorder();
    const texts = () => (ctx.fillText as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
    const options = { boxes: true, labels: true, trails: false, lines: false, zones: false };
    const tiny = track(3, { box: [0.1, 0.2, 0.12, 0.3] }); // 0.02 of 640 px is under 16 px
    drawOverlay(ctx, { width: 400, height: 225 }, { lines: [], zones: [], tracks: [track(1), tiny] }, options, new Map(), colors);
    expect(texts()).toEqual(["car"]);
    drawOverlay(ctx, size, { lines: [], zones: [], tracks: [track(1)] }, options, new Map(), colors);
    expect(texts()).toEqual(["car", "#1 car 90%"]);
  });
});

describe("trails", () => {
  it("grows with each frame and forgets old points", () => {
    const trails = new Map<number, Trail>();
    updateTrails(trails, [track(1)], 0);
    updateTrails(trails, [track(1, { box: [0.2, 0.2, 0.4, 0.6] })], 500);
    expect(trails.get(1)?.points).toHaveLength(2);
    updateTrails(trails, [track(1)], 2400); // the first point is older than 2 s now, the second is not
    expect(trails.get(1)?.points).toHaveLength(2);
  });

  it("drops a track that has gone once its trail has faded", () => {
    const trails = new Map<number, Trail>();
    updateTrails(trails, [track(1)], 0);
    updateTrails(trails, [], 1000);
    expect(trails.has(1)).toBe(true); // still fading
    updateTrails(trails, [], 3500);
    expect(trails.has(1)).toBe(false);
  });

  it("does not trail a track that is not confirmed yet", () => {
    const trails = new Map<number, Trail>();
    updateTrails(trails, [track(2, { confirmed: false })], 0);
    expect(trails.size).toBe(0);
  });
});

function recorder() {
  const calls: string[] = [];
  const record = (name: string) => vi.fn(() => void calls.push(name));
  const ctx = {
    save: record("save"), restore: record("restore"), clearRect: record("clearRect"),
    beginPath: record("beginPath"), moveTo: record("moveTo"), lineTo: record("lineTo"),
    stroke: record("stroke"), fill: record("fill"), closePath: record("closePath"),
    fillRect: record("fillRect"), strokeRect: record("strokeRect"), fillText: record("fillText"),
    measureText: vi.fn(() => ({ width: 40 }) as TextMetrics), setLineDash: record("setLineDash"),
    strokeStyle: "", fillStyle: "", lineWidth: 1, font: "", globalAlpha: 1,
    lineJoin: "round", lineCap: "round", textBaseline: "alphabetic",
  } as unknown as Canvas2D;
  return { ctx, calls };
}

const colors: OverlayColors = { forClass: () => "#2a78d6", line: "#ffffff", chipEdge: "#0b0b0b", exclusionFill: "rgba(0, 0, 0, 0.4)" };
const scene = {
  lines: [{ id: "l", name: "Main", a: [0.2, 0.5] as [number, number], b: [0.8, 0.5] as [number, number] }],
  zones: [],
  tracks: [track(1), track(2, { confirmed: false })],
};

describe("drawOverlay", () => {
  const everything = { boxes: true, labels: true, trails: true, lines: true, zones: true };

  it("clears the canvas first and draws boxes, labels and lines when all are on", () => {
    const { ctx, calls } = recorder();
    drawOverlay(ctx, size, scene, everything, new Map(), colors);
    expect(calls[0]).toBe("clearRect");
    expect(calls.filter((c) => c === "strokeRect")).toHaveLength(2); // both tracks get a box
    expect(calls.filter((c) => c === "fillText")).toHaveLength(2); // the confirmed track and the line
    expect(calls).toContain("stroke");
  });

  it("draws nothing but the clearing when everything is off", () => {
    const { ctx, calls } = recorder();
    drawOverlay(ctx, size, scene, { boxes: false, labels: false, trails: false, lines: false, zones: false }, new Map(), colors);
    expect(calls).toEqual(["clearRect"]);
  });

  it("respects each switch on its own", () => {
    const only = (options: Partial<typeof everything>) => {
      const { ctx, calls } = recorder();
      drawOverlay(ctx, size, scene, { boxes: false, labels: false, trails: false, lines: false, zones: false, ...options }, new Map(), colors);
      return calls;
    };
    expect(only({ boxes: true })).toContain("strokeRect");
    expect(only({ boxes: true })).not.toContain("fillText");
    expect(only({ lines: true })).not.toContain("strokeRect");
    expect(only({ lines: true, labels: true })).toContain("fillText");
  });

  it("labels only tracks that are confirmed", () => {
    const { ctx } = recorder();
    drawOverlay(ctx, size, { lines: [], zones: [], tracks: [track(1), track(2, { confirmed: false })] }, everything, new Map(), colors);
    const texts = (ctx.fillText as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);
    expect(texts).toEqual(["#1 car 90%"]);
  });

  it("draws a trail only with two or more points", () => {
    const trails = new Map<number, Trail>([
      [1, { label: "car", points: [{ p: [0.1, 0.5], t: 0 }] }],
      [2, { label: "car", points: [{ p: [0.1, 0.5], t: 0 }, { p: [0.2, 0.6], t: 100 }] }],
    ]);
    const { ctx, calls } = recorder();
    drawOverlay(ctx, size, { lines: [], zones: [], tracks: [] }, { boxes: false, labels: false, trails: true, lines: false, zones: false }, trails, colors);
    expect(calls.filter((c) => c === "moveTo")).toHaveLength(1);
    expect(calls.filter((c) => c === "lineTo")).toHaveLength(1);
  });
});

describe("zones", () => {
  const road: LiveZone = { id: "d", name: "The road", kind: "detection", points: [[0.1, 0.4], [0.9, 0.4], [0.9, 0.9], [0.1, 0.9]] };
  const parking: LiveZone = { id: "x", name: "Parking", kind: "exclusion", points: [[0.1, 0.5], [0.3, 0.5], [0.3, 0.8]] };
  const zonesOnly = { boxes: false, labels: false, trails: false, lines: false, zones: true };
  const draw = (zones: LiveZone[], options: Partial<typeof zonesOnly> = {}, at = size) => {
    const { ctx, calls } = recorder();
    drawOverlay(ctx, at, { lines: [], zones, tracks: [] }, { ...zonesOnly, ...options }, new Map(), colors);
    return { ctx, calls };
  };
  const texts = (ctx: Canvas2D) => (ctx.fillText as unknown as ReturnType<typeof vi.fn>).mock.calls.map((c) => c[0] as string);

  it("outlines a detection zone with a dashed closed line, a dark halo under a white one, and no shading", () => {
    const { ctx, calls } = draw([road]);
    expect(calls.filter((c) => c === "closePath")).toHaveLength(2); // the halo and the line
    expect(calls.filter((c) => c === "stroke")).toHaveLength(2);
    expect(calls).toContain("setLineDash");
    expect(calls).not.toContain("fill");
    expect((ctx.moveTo as unknown as ReturnType<typeof vi.fn>).mock.calls[0]).toEqual([64, 144]); // 0.1 and 0.4 of the picture
  });

  it("shades an exclusion zone as well, so that it reads as 'not looked at'", () => {
    const { calls } = draw([parking]);
    expect(calls.filter((c) => c === "fill")).toHaveLength(1);
    expect(calls.filter((c) => c === "stroke")).toHaveLength(2);
  });

  it("labels each zone with its name, or with what it is when it has none", () => {
    expect(texts(draw([road, parking], { labels: true }).ctx)).toEqual(["The road", "Ignored: Parking"]);
    const unnamed = [{ ...road, name: "" }, { ...parking, name: "" }];
    expect(texts(draw(unnamed, { labels: true }).ctx)).toEqual(["Detection zone", "Ignored area"]);
    expect(texts(draw([road], { labels: false }).ctx)).toEqual([]);
  });

  it("draws no zones when they are switched off, and skips one with too few corners", () => {
    expect(draw([road, parking], { zones: false }).calls).toEqual(["clearRect"]);
    expect(draw([{ ...road, points: [[0.1, 0.1], [0.2, 0.2]] }]).calls).toEqual(["clearRect"]);
  });

  it("is drawn under the lines, so that a line across a zone stays visible", () => {
    // Only the zones are wrapped in save/restore here (no tracks, so no boxes or trails), so the
    // line's strokes must come after that restore, and every closing of an outline before it.
    const { ctx, calls } = recorder();
    const options = { boxes: false, labels: false, trails: false, lines: true, zones: true };
    drawOverlay(ctx, size, { ...scene, tracks: [], zones: [road] }, options, new Map(), colors);
    const restored = calls.indexOf("restore");
    expect(restored).toBeGreaterThan(calls.lastIndexOf("closePath"));
    expect(calls.slice(restored)).toContain("stroke");
  });
});

describe("zoneLabel", () => {
  it("names the kind when the zone has no name of its own", () => {
    expect(zoneLabel({ kind: "detection", name: "" })).toBe("Detection zone");
    expect(zoneLabel({ kind: "exclusion", name: "" })).toBe("Ignored area");
    expect(zoneLabel({ kind: "exclusion", name: "Parking" })).toBe("Ignored: Parking");
  });
});
