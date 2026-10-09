import { describe, expect, it } from "vitest";
import {
  addLine,
  addStop,
  addZone,
  canAddCorner,
  canRemoveCorner,
  cornerCount,
  deleteElement,
  edgeMiddle,
  flipLine,
  flipStop,
  insertCorner,
  moveElement,
  movePoint,
  pointsOf,
  removeCorner,
  renameElement,
  resetStopNumbers,
  setStopNumber,
  splitLongestEdge,
  toggleLineClass,
  toggleStopClass,
} from "./editor";
import { lineOf, profileOf, rectangle, stopOf, zoneOf } from "./fixtures";
import { outlineProblem } from "./geometry";
import { DEFAULT_LINE_CLASSES, MAX_ELEMENTS, MAX_NAME_LENGTH, MAX_POINTS, emptyProfile, problems } from "./profile";
import type { Profile } from "./types";

const fixedId = () => "abc123";

describe("adding a line", () => {
  it("puts a ready-to-drag line across the middle, counting vehicles by default", () => {
    const added = addLine(emptyProfile(), fixedId)!;
    expect(added.ref).toEqual({ kind: "line", id: "line-abc123" });
    expect(added.profile.lines).toEqual([
      {
        id: "line-abc123",
        name: "Counting line 1",
        a: [0.25, 0.5],
        b: [0.75, 0.5],
        classes: [...DEFAULT_LINE_CLASSES],
        point: "ground",
        margin: null,
        persistence: null,
      },
    ]);
  });

  it("does not touch the profile it was given, or share the default class list", () => {
    const before = emptyProfile();
    const added = addLine(before, fixedId)!;
    expect(before.lines).toEqual([]);
    expect(added.profile.lines[0]!.classes).not.toBe(DEFAULT_LINE_CLASSES);
  });

  it("starts each new line a little lower than the last, so none hides another", () => {
    let profile = emptyProfile();
    const ys: number[] = [];
    for (let i = 0; i < 9; i += 1) {
      profile = addLine(profile)!.profile;
      ys.push(profile.lines[i]!.a[1]);
    }
    expect(ys.slice(0, 3).map((y) => Number(y.toFixed(2)))).toEqual([0.5, 0.56, 0.62]);
    expect(Math.max(...ys)).toBeLessThanOrEqual(1);
    expect(ys[8]).toBe(ys[0]); // after eight it starts over
    expect(new Set(profile.lines.map((line) => line.id)).size).toBe(9);
  });

  it("refuses when the profile is full", () => {
    const full = profileOf({ lines: Array.from({ length: MAX_ELEMENTS }, (_, i) => lineOf(`l${i}`)) });
    expect(addLine(full)).toBeNull();
  });
});

describe("adding a zone", () => {
  it("makes a rectangle: the road for a detection zone, a smaller patch beside it for an exclusion zone", () => {
    const detection = addZone(emptyProfile(), "detection", fixedId)!;
    expect(detection.ref).toEqual({ kind: "detection", id: "area-abc123" });
    expect(detection.profile.detection_zones[0]).toEqual({
      id: "area-abc123",
      name: "Detection zone 1",
      points: rectangle(0.1, 0.3, 0.9, 0.9),
    });
    const exclusion = addZone(emptyProfile(), "exclusion", fixedId)!;
    expect(exclusion.profile.exclusion_zones[0]).toEqual({
      id: "ignore-abc123",
      name: "Exclusion zone 1",
      points: rectangle(0.1, 0.55, 0.3, 0.85),
    });
    expect(exclusion.profile.detection_zones).toEqual([]);
  });

  it("always starts with a zone the server accepts, however many there already are", () => {
    for (const kind of ["detection", "exclusion"] as const) {
      let profile: Profile = emptyProfile();
      for (let i = 0; i < 12; i += 1) {
        profile = addZone(profile, kind)!.profile;
        const zone = (kind === "detection" ? profile.detection_zones : profile.exclusion_zones).at(-1)!;
        expect(outlineProblem(zone.points), `${kind} #${i + 1}`).toBeNull();
        for (const [x, y] of zone.points) {
          expect(x).toBeGreaterThanOrEqual(0);
          expect(x).toBeLessThanOrEqual(1);
          expect(y).toBeGreaterThanOrEqual(0);
          expect(y).toBeLessThanOrEqual(1);
        }
      }
      expect(problems(profile).filter((p) => p.level === "error")).toEqual([]);
    }
  });

  it("starts later zones of a kind in a different place", () => {
    const first = addZone(emptyProfile(), "detection", fixedId)!.profile;
    const second = addZone(first, "detection", () => "def456")!.profile;
    expect(second.detection_zones[1]!.points).not.toEqual(second.detection_zones[0]!.points);
    expect(second.detection_zones[1]!.name).toBe("Detection zone 2");
  });

  it("refuses when the profile is full of that kind, but not of the other", () => {
    const full = profileOf({ detection: Array.from({ length: MAX_ELEMENTS }, (_, i) => zoneOf(`d${i}`)) });
    expect(addZone(full, "detection")).toBeNull();
    expect(addZone(full, "exclusion")).not.toBeNull();
  });
});

describe("moving one point", () => {
  const profile = profileOf({ lines: [lineOf("l1")], detection: [zoneOf("d1")] });

  it("moves either end of a line", () => {
    const a = movePoint(profile, { kind: "line", id: "l1" }, 0, [0.1, 0.4]);
    expect([a.lines[0]!.a, a.lines[0]!.b]).toEqual([[0.1, 0.4], [0.8, 0.5]]);
    const b = movePoint(profile, { kind: "line", id: "l1" }, 1, [0.9, 0.6]);
    expect([b.lines[0]!.a, b.lines[0]!.b]).toEqual([[0.2, 0.5], [0.9, 0.6]]);
  });

  it("moves one corner of a zone and leaves the others", () => {
    const moved = movePoint(profile, { kind: "detection", id: "d1" }, 2, [0.95, 0.9]);
    expect(moved.detection_zones[0]!.points).toEqual([[0.2, 0.2], [0.8, 0.2], [0.95, 0.9], [0.2, 0.8]]);
  });

  it("keeps the point inside the picture", () => {
    const moved = movePoint(profile, { kind: "line", id: "l1" }, 0, [-0.3, 1.4]);
    expect(moved.lines[0]!.a).toEqual([0, 1]);
  });

  it("gives back the same profile when nothing changes", () => {
    expect(movePoint(profile, { kind: "line", id: "l1" }, 0, [0.2, 0.5])).toBe(profile);
    expect(movePoint(profile, { kind: "line", id: "l1" }, 2, [0.5, 0.5])).toBe(profile); // a line has two ends
    expect(movePoint(profile, { kind: "detection", id: "d1" }, 9, [0.5, 0.5])).toBe(profile);
    expect(movePoint(profile, { kind: "line", id: "gone" }, 0, [0.5, 0.5])).toBe(profile);
  });

  it("does not change the profile it was given", () => {
    const before = structuredClone(profile);
    movePoint(profile, { kind: "line", id: "l1" }, 0, [0.1, 0.1]);
    expect(profile).toEqual(before);
  });
});

describe("moving a whole element", () => {
  it("shifts every point by the same amount", () => {
    const profile = profileOf({ lines: [lineOf("l1")], detection: [zoneOf("d1")] });
    const line = moveElement(profile, { kind: "line", id: "l1" }, 0.1, -0.2);
    expect(line.lines[0]!.a[0]).toBeCloseTo(0.3);
    expect(line.lines[0]!.a[1]).toBeCloseTo(0.3);
    expect(line.lines[0]!.b[0]).toBeCloseTo(0.9);
    expect(line.lines[0]!.b[1]).toBeCloseTo(0.3);
    const zone = moveElement(profile, { kind: "detection", id: "d1" }, -0.1, 0.1);
    expect(zone.detection_zones[0]!.points[0]![0]).toBeCloseTo(0.1);
    expect(zone.detection_zones[0]!.points[2]![1]).toBeCloseTo(0.9);
  });

  it("stops at the edge of the picture and keeps its shape instead of squashing against it", () => {
    const profile = profileOf({ detection: [zoneOf("d1", rectangle(0.1, 0.2, 0.5, 0.6))] });
    const left = moveElement(profile, { kind: "detection", id: "d1" }, -0.5, 0);
    expect(left.detection_zones[0]!.points).toEqual(rectangle(0, 0.2, 0.4, 0.6));
    const down = moveElement(profile, { kind: "detection", id: "d1" }, 0, 5);
    const ys = down.detection_zones[0]!.points.map((p) => p[1]);
    expect(Math.max(...ys)).toBeLessThanOrEqual(1);
    expect(Math.max(...ys) - Math.min(...ys)).toBeCloseTo(0.4);
  });

  it("never lets a point leave the picture by a rounding error", () => {
    const profile = profileOf({ detection: [zoneOf("d1", rectangle(0.1, 0.1, 0.30000000000000004, 0.7))] });
    const moved = moveElement(profile, { kind: "detection", id: "d1" }, 10, 10);
    for (const [x, y] of moved.detection_zones[0]!.points) {
      expect(x).toBeLessThanOrEqual(1);
      expect(y).toBeLessThanOrEqual(1);
    }
  });

  it("gives back the same profile when it cannot move that way", () => {
    const profile = profileOf({ detection: [zoneOf("d1", rectangle(0.5, 0.2, 1, 0.6))] });
    expect(moveElement(profile, { kind: "detection", id: "d1" }, 0.3, 0)).toBe(profile);
    expect(moveElement(profile, { kind: "detection", id: "d1" }, 0, 0)).toBe(profile);
    expect(moveElement(profile, { kind: "line", id: "gone" }, 0.1, 0.1)).toBe(profile);
  });
});

describe("corners of a zone", () => {
  const ref = { kind: "detection", id: "d1" } as const;
  const square = profileOf({ detection: [zoneOf("d1")] });

  it("adds one after a given corner, which becomes the next index", () => {
    const grown = insertCorner(square, ref, 1, [0.9, 0.5]);
    expect(grown.detection_zones[0]!.points).toEqual([[0.2, 0.2], [0.8, 0.2], [0.9, 0.5], [0.8, 0.8], [0.2, 0.8]]);
  });

  it("can add a corner on the closing edge", () => {
    const grown = insertCorner(square, ref, 3, [0.1, 0.5]);
    expect(grown.detection_zones[0]!.points.at(-1)).toEqual([0.1, 0.5]);
    expect(grown.detection_zones[0]!.points).toHaveLength(5);
  });

  it("refuses corners on a line, past the last index, or beyond the most a zone may have", () => {
    const withLine = profileOf({ lines: [lineOf("l1")], detection: [zoneOf("d1")] });
    expect(insertCorner(withLine, { kind: "line", id: "l1" }, 0, [0.5, 0.5])).toBe(withLine);
    expect(insertCorner(square, ref, 9, [0.5, 0.5])).toBe(square);
    const round = Array.from({ length: MAX_POINTS }, (_, i): [number, number] => [
      0.5 + 0.4 * Math.cos((i / MAX_POINTS) * 2 * Math.PI),
      0.5 + 0.4 * Math.sin((i / MAX_POINTS) * 2 * Math.PI),
    ]);
    const crowded = profileOf({ detection: [zoneOf("d1", round)] });
    expect(canAddCorner(crowded, ref)).toBe(false);
    expect(insertCorner(crowded, ref, 0, [0.5, 0.5])).toBe(crowded);
  });

  it("removes one, but a zone keeps at least three", () => {
    const smaller = removeCorner(square, ref, 0);
    expect(smaller.detection_zones[0]!.points).toEqual([[0.8, 0.2], [0.8, 0.8], [0.2, 0.8]]);
    expect(canRemoveCorner(smaller, ref)).toBe(false);
    expect(removeCorner(smaller, ref, 0)).toBe(smaller);
    expect(removeCorner(square, ref, 7)).toBe(square);
  });

  it("knows what can be done to which element", () => {
    const withLine = profileOf({ lines: [lineOf("l1")], detection: [zoneOf("d1")] });
    expect(canAddCorner(withLine, ref)).toBe(true);
    expect(canRemoveCorner(withLine, ref)).toBe(true);
    expect(canAddCorner(withLine, { kind: "line", id: "l1" })).toBe(false);
    expect(canRemoveCorner(withLine, { kind: "line", id: "l1" })).toBe(false);
    expect(canAddCorner(withLine, { kind: "detection", id: "gone" })).toBe(false);
  });

  it("splits the longest edge in the middle, which keeps the shape and the area", () => {
    const wide = profileOf({ detection: [zoneOf("d1", rectangle(0.1, 0.2, 0.9, 0.4))] }); // the top and bottom are longest
    const split = splitLongestEdge(wide, ref)!;
    expect(split.index).toBe(1);
    expect(split.profile.detection_zones[0]!.points).toEqual([[0.1, 0.2], [0.5, 0.2], [0.9, 0.2], [0.9, 0.4], [0.1, 0.4]]);
    expect(problems(split.profile)).toEqual([]);
    const closing = profileOf({ detection: [zoneOf("d1", [[0.1, 0.1], [0.3, 0.1], [0.3, 0.3], [0.2, 0.9]])] });
    expect(splitLongestEdge(closing, ref)!.index).toBe(4); // the edge from the last corner back to the first
  });

  it("cannot split anything on a line, in a full zone or in a zone that is not there", () => {
    const withLine = profileOf({ lines: [lineOf("l1")], detection: [zoneOf("d1")] });
    expect(splitLongestEdge(withLine, { kind: "line", id: "l1" })).toBeNull();
    expect(splitLongestEdge(withLine, { kind: "detection", id: "gone" })).toBeNull();
  });

  it("finds the middle of an edge, the last one closing the outline", () => {
    expect(edgeMiddle(rectangle(0, 0, 1, 1), 0)).toEqual([0.5, 0]);
    expect(edgeMiddle(rectangle(0, 0, 1, 1), 3)).toEqual([0, 0.5]);
  });
});

describe("what an element is made of", () => {
  it("lists the points of a line or a zone, and none for something that is not there", () => {
    const profile = profileOf({ lines: [lineOf("l1")], exclusion: [zoneOf("x1")] });
    expect(pointsOf(profile, { kind: "line", id: "l1" })).toEqual([[0.2, 0.5], [0.8, 0.5]]);
    expect(pointsOf(profile, { kind: "exclusion", id: "x1" })).toHaveLength(4);
    expect(pointsOf(profile, { kind: "exclusion", id: "gone" })).toEqual([]);
  });
});

describe("properties", () => {
  it("renames an element, and does nothing when the name is the same", () => {
    const profile = profileOf({ lines: [lineOf("l1", { name: "A" })], exclusion: [zoneOf("x1")] });
    expect(renameElement(profile, { kind: "line", id: "l1" }, "Main street").lines[0]!.name).toBe("Main street");
    expect(renameElement(profile, { kind: "exclusion", id: "x1" }, "Parking").exclusion_zones[0]!.name).toBe("Parking");
    expect(renameElement(profile, { kind: "line", id: "l1" }, "A")).toBe(profile);
  });

  it("cuts a name that is longer than the server accepts", () => {
    const profile = profileOf({ lines: [lineOf("l1")] });
    const renamed = renameElement(profile, { kind: "line", id: "l1" }, "x".repeat(200));
    expect(renamed.lines[0]!.name).toHaveLength(MAX_NAME_LENGTH);
  });

  it("ticks and unticks the kinds a line counts, in the usual order", () => {
    const profile = profileOf({ lines: [lineOf("l1", { classes: ["car", "truck"] })] });
    const withBus = toggleLineClass(profile, "l1", "bus");
    expect(withBus.lines[0]!.classes).toEqual(["car", "truck", "bus"]);
    const withBicycleFirst = toggleLineClass(toggleLineClass(profile, "l1", "bicycle"), "l1", "motorcycle");
    expect(withBicycleFirst.lines[0]!.classes).toEqual(["car", "truck", "motorcycle", "bicycle"]);
    expect(toggleLineClass(withBus, "l1", "bus").lines[0]!.classes).toEqual(["car", "truck"]);
  });

  it("starts from the listed kinds when a line counted every kind", () => {
    const profile = profileOf({ lines: [lineOf("l1", { classes: null })] });
    expect(toggleLineClass(profile, "l1", "person").lines[0]!.classes).toEqual([
      "car", "truck", "bus", "motorcycle", "bicycle",
    ]);
  });

  it("keeps kinds the editor does not list, after the others", () => {
    const profile = profileOf({ lines: [lineOf("l1", { classes: ["dog", "car"] })] });
    expect(toggleLineClass(profile, "l1", "bus").lines[0]!.classes).toEqual(["car", "bus", "dog"]);
  });

  it("lets the last kind be unticked, which the problem list then points out", () => {
    const profile = profileOf({ lines: [lineOf("l1", { classes: ["car"] })] });
    const none = toggleLineClass(profile, "l1", "car");
    expect(none.lines[0]!.classes).toEqual([]);
    expect(problems(none).map((p) => p.level)).toEqual(["error"]);
  });

  it("flips a line round, and flipping twice gives it back", () => {
    const profile = profileOf({ lines: [lineOf("l1", { a: [0.1, 0.2], b: [0.7, 0.9] })] });
    const flipped = flipLine(profile, "l1");
    expect([flipped.lines[0]!.a, flipped.lines[0]!.b]).toEqual([[0.7, 0.9], [0.1, 0.2]]);
    expect(flipLine(flipped, "l1")).toEqual(profile);
    expect(flipLine(profile, "gone")).toBe(profile);
  });
});

describe("deleting", () => {
  const profile = profileOf({ lines: [lineOf("l1"), lineOf("l2")], detection: [zoneOf("d1")], exclusion: [zoneOf("x1")] });

  it("removes just the element asked for, of any kind", () => {
    expect(deleteElement(profile, { kind: "line", id: "l1" }).lines.map((l) => l.id)).toEqual(["l2"]);
    expect(deleteElement(profile, { kind: "detection", id: "d1" }).detection_zones).toEqual([]);
    expect(deleteElement(profile, { kind: "exclusion", id: "x1" }).exclusion_zones).toEqual([]);
    expect(deleteElement(profile, { kind: "line", id: "l1" }).detection_zones).toHaveLength(1);
  });

  it("does nothing for an element that is not there, or the wrong kind", () => {
    expect(deleteElement(profile, { kind: "line", id: "gone" })).toBe(profile);
    expect(deleteElement(profile, { kind: "detection", id: "l1" })).toBe(profile);
  });
});

// --- stop signs ---------------------------------------------------------------------------------------

describe("adding a stop sign", () => {
  it("puts a zone and, at its edge, a line that vehicles drive across, ready to be dragged", () => {
    const added = addStop(emptyProfile(), fixedId)!;
    expect(added.ref).toEqual({ kind: "stop", id: "stop-abc123" });
    const [sign] = added.profile.stop_signs;
    expect(sign).toMatchObject({ id: "stop-abc123", name: "Stop sign 1", direction: 1, hold_s: 1, still_radius: 0.08, roll_speed: 0.7, min_observed_s: 0.8 });
    expect(sign!.classes).toEqual(["car", "truck", "bus", "motorcycle"]);
    expect(sign!.approach).toHaveLength(4);
    expect(problems(added.profile)).toEqual([]); // sensible as it is: the zone behind the line, close to it
    // no handle sits exactly on another, so each can be picked up
    const points = pointsOf(added.profile, added.ref).map((p) => p.join(","));
    expect(new Set(points).size).toBe(points.length);
    // the line is on the side of the zone's right edge, past both of its ends
    expect(sign!.a[0]).toBe(0.5);
    expect(sign!.a[1]).toBeGreaterThan(Math.max(...sign!.approach.map((p) => p[1])));
    expect(sign!.b[1]).toBeLessThan(Math.min(...sign!.approach.map((p) => p[1])));
  });

  it("starts each later sign lower, and does not share the default kinds with another", () => {
    const first = addStop(emptyProfile(), fixedId)!.profile;
    const second = addStop(first, () => "def456")!.profile;
    expect(second.stop_signs[1]!.approach[0]![1]).toBeGreaterThan(second.stop_signs[0]!.approach[0]![1]);
    expect(second.stop_signs[1]!.name).toBe("Stop sign 2");
    expect(second.stop_signs[0]!.classes).not.toBe(second.stop_signs[1]!.classes);
  });

  it("does nothing when there are as many as there can be", () => {
    const full = profileOf({ stops: Array.from({ length: MAX_ELEMENTS }, (_, i) => stopOf(`s${i}`)) });
    expect(addStop(full)).toBeNull();
  });
});

describe("a stop sign's points", () => {
  const profile = profileOf({ stops: [stopOf("s1")] });
  const ref = { kind: "stop", id: "s1" } as const;

  it("are the corners of its zone and then the two ends of its line", () => {
    expect(pointsOf(profile, ref)).toEqual([...rectangle(0.15, 0.45, 0.5, 0.75), [0.5, 0.75], [0.5, 0.45]]);
    expect(cornerCount(profile, ref)).toBe(4);
    expect(cornerCount(profile, { kind: "line", id: "x" })).toBe(0);
    expect(cornerCount(profileOf({ detection: [zoneOf("d1")] }), { kind: "detection", id: "d1" })).toBe(4);
    expect(pointsOf(profile, { kind: "stop", id: "missing" })).toEqual([]);
  });

  it("move one at a time: a corner moves the zone only, an end of the line moves the line only", () => {
    const corner = movePoint(profile, ref, 1, [0.55, 0.4]).stop_signs[0]!;
    expect(corner.approach[1]).toEqual([0.55, 0.4]);
    expect([corner.a, corner.b]).toEqual([[0.5, 0.75], [0.5, 0.45]]);
    const end = movePoint(profile, ref, 4, [0.52, 0.8]).stop_signs[0]!; // index 4: the line's `a`
    expect(end.a).toEqual([0.52, 0.8]);
    expect(end.approach).toEqual(rectangle(0.15, 0.45, 0.5, 0.75));
    expect(movePoint(profile, ref, 5, [0.9, 0.1]).stop_signs[0]!.b).toEqual([0.9, 0.1]);
    // the last corner is a corner, not the line: the one next to the first line end
    const last = movePoint(profile, ref, 3, [0.2, 0.8]).stop_signs[0]!;
    expect(last.approach[3]).toEqual([0.2, 0.8]);
    expect([last.a, last.b]).toEqual([[0.5, 0.75], [0.5, 0.45]]);
  });

  it("stay inside the picture, and a move that changes nothing returns the very same profile", () => {
    expect(movePoint(profile, ref, 4, [3, -2]).stop_signs[0]!.a).toEqual([1, 0]);
    expect(movePoint(profile, ref, 4, [0.5, 0.75])).toBe(profile);
    expect(movePoint(profile, ref, 0, [0.15, 0.45])).toBe(profile);
    expect(movePoint(profile, ref, 6, [0.1, 0.1])).toBe(profile); // there is no point 6
    expect(movePoint(profile, ref, -1, [0.1, 0.1])).toBe(profile);
    expect(movePoint(profile, { kind: "stop", id: "missing" }, 0, [0.1, 0.1])).toBe(profile);
  });

  it("move together, as a whole, and stop at the edge of the picture", () => {
    const moved = moveElement(profile, ref, 0.1, 0.05).stop_signs[0]!;
    expect(moved.approach[0]).toEqual([expect.closeTo(0.25), expect.closeTo(0.5)]);
    expect(moved.a).toEqual([expect.closeTo(0.6), expect.closeTo(0.8)]);
    expect(moved.b).toEqual([expect.closeTo(0.6), expect.closeTo(0.5)]);
    const pushed = moveElement(profile, ref, 5, 0).stop_signs[0]!;
    expect(Math.max(...pushed.approach.map((p) => p[0]), pushed.a[0], pushed.b[0])).toBe(1);
    expect(pushed.a[0] - pushed.approach[0]![0]).toBeCloseTo(0.35); // the shape is kept
    expect(moveElement(profile, ref, 0, 0)).toBe(profile);
  });
});

describe("a stop sign's approach zone", () => {
  const profile = profileOf({ stops: [stopOf("s1")] });
  const ref = { kind: "stop", id: "s1" } as const;

  it("can take more corners, and lose them down to three, but its line's ends are not corners", () => {
    expect(canAddCorner(profile, ref)).toBe(true);
    expect(canRemoveCorner(profile, ref)).toBe(true);
    expect(canRemoveCorner(profile, ref, 3)).toBe(true);
    expect(canRemoveCorner(profile, ref, 4)).toBe(false); // an end of the stop line
    expect(canRemoveCorner(profile, ref, -1)).toBe(false);
    const grown = insertCorner(profile, ref, 1, edgeMiddle(pointsOf(profile, ref), 1));
    expect(grown.stop_signs[0]!.approach).toHaveLength(5);
    expect(grown.stop_signs[0]!.approach[2]).toEqual([0.5, 0.6]); // between corners 1 and 2
    expect([grown.stop_signs[0]!.a, grown.stop_signs[0]!.b]).toEqual([[0.5, 0.75], [0.5, 0.45]]); // the line is left alone
    const shrunk = removeCorner(grown, ref, 2);
    expect(shrunk.stop_signs[0]!.approach).toEqual(rectangle(0.15, 0.45, 0.5, 0.75));
    const triangle = removeCorner(profile, ref, 3);
    expect(triangle.stop_signs[0]!.approach).toHaveLength(3);
    expect(removeCorner(triangle, ref, 0)).toBe(triangle); // three is the least
    expect(removeCorner(profile, ref, 4)).toBe(profile); // the end of the line is not a corner
  });

  it("is refused a corner beyond the most there can be", () => {
    const many = profileOf({ stops: [stopOf("s1", { approach: Array.from({ length: MAX_POINTS }, (_, i) => [0.1 + i * 0.02, 0.5 + (i % 2) * 0.1]) as [number, number][] })] });
    expect(canAddCorner(many, ref)).toBe(false);
    expect(insertCorner(many, ref, 0, [0.3, 0.3])).toBe(many);
    expect(splitLongestEdge(many, ref)).toBeNull();
  });

  it("is split along its longest edge, the line's own length not counting", () => {
    const split = splitLongestEdge(profile, ref)!;
    expect(split.profile.stop_signs[0]!.approach).toHaveLength(5);
    expect(split.index).toBe(1); // the top edge, 0.35 long, is the first of the two longest
    expect(split.profile.stop_signs[0]!.approach[1]).toEqual([expect.closeTo(0.325), 0.45]);
  });
});

describe("a stop sign's properties", () => {
  const profile = profileOf({ stops: [stopOf("s1")], lines: [lineOf("l1")] });

  it("has a name, kept to the longest the server takes", () => {
    expect(renameElement(profile, { kind: "stop", id: "s1" }, "Elm Street").stop_signs[0]!.name).toBe("Elm Street");
    expect(renameElement(profile, { kind: "stop", id: "s1" }, "x".repeat(200)).stop_signs[0]!.name).toHaveLength(MAX_NAME_LENGTH);
    expect(renameElement(profile, { kind: "stop", id: "s1" }, "")).toBe(profile); // already empty
  });

  it("turns round: the way through, and so the side its zone should be on", () => {
    const flipped = flipStop(profile, "s1");
    expect(flipped.stop_signs[0]!.direction).toBe(-1);
    expect(flipStop(flipped, "s1").stop_signs[0]!.direction).toBe(1);
    expect(flipStop(profile, "missing")).toBe(profile);
    expect(problems(flipped).some((p) => p.level === "warning")).toBe(true); // now the zone is on the wrong side
  });

  it("has kinds that must stop, ticked in the usual order, bicycles among them but not people", () => {
    const withBicycle = toggleStopClass(profile, "s1", "bicycle");
    expect(withBicycle.stop_signs[0]!.classes).toEqual(["car", "truck", "bus", "motorcycle", "bicycle"]);
    const withoutCar = toggleStopClass(withBicycle, "s1", "car");
    expect(withoutCar.stop_signs[0]!.classes).toEqual(["truck", "bus", "motorcycle", "bicycle"]);
    expect(toggleStopClass(withoutCar, "s1", "car").stop_signs[0]!.classes).toEqual(["car", "truck", "bus", "motorcycle", "bicycle"]);
    const none = ["car", "truck", "bus", "motorcycle"].reduce((p, name) => toggleStopClass(p, "s1", name), profile);
    expect(none.stop_signs[0]!.classes).toEqual([]);
    expect(hasError(none)).toBe(true); // which the form says, and the server would refuse
    expect(toggleStopClass(profile, "missing", "car")).toBe(profile);
  });

  it("has numbers that are set, in thousandths, only inside what the server takes", () => {
    expect(setStopNumber(profile, "s1", "hold_s", 2.5).stop_signs[0]!.hold_s).toBe(2.5);
    expect(setStopNumber(profile, "s1", "still_radius", 0.12345).stop_signs[0]!.still_radius).toBe(0.123);
    expect(setStopNumber(profile, "s1", "hold_s", 0.1)).toBe(profile); // under the least
    expect(setStopNumber(profile, "s1", "hold_s", 11)).toBe(profile); // over the most
    expect(setStopNumber(profile, "s1", "roll_speed", Number.NaN)).toBe(profile);
    expect(setStopNumber(profile, "s1", "roll_speed", Number.POSITIVE_INFINITY)).toBe(profile);
    expect(setStopNumber(profile, "s1", "min_observed_s", 0.8)).toBe(profile); // unchanged
    expect(setStopNumber(profile, "missing", "hold_s", 2)).toBe(profile);
  });

  it("goes back to what a stop is to begin with", () => {
    const tuned = setStopNumber(setStopNumber(profile, "s1", "hold_s", 3), "s1", "roll_speed", 2);
    expect(resetStopNumbers(tuned, "s1").stop_signs[0]).toMatchObject({ hold_s: 1, still_radius: 0.08, roll_speed: 0.7, min_observed_s: 0.8 });
    expect(resetStopNumbers(profile, "s1")).toBe(profile);
  });

  it("is deleted with both its parts, and only itself", () => {
    const both = profileOf({ stops: [stopOf("s1"), stopOf("s2")], lines: [lineOf("l1")] });
    const gone = deleteElement(both, { kind: "stop", id: "s1" });
    expect(gone.stop_signs.map((s) => s.id)).toEqual(["s2"]);
    expect(gone.lines).toHaveLength(1);
    expect(deleteElement(both, { kind: "stop", id: "missing" })).toBe(both);
  });
});

function hasError(profile: Profile): boolean {
  return problems(profile).some((p) => p.level === "error");
}
