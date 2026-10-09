import { describe, expect, it } from "vitest";
import {
  addLine,
  addZone,
  canAddCorner,
  canRemoveCorner,
  deleteElement,
  edgeMiddle,
  flipLine,
  insertCorner,
  moveElement,
  movePoint,
  pointsOf,
  removeCorner,
  renameElement,
  splitLongestEdge,
  toggleLineClass,
} from "./editor";
import { lineOf, profileOf, rectangle, zoneOf } from "./fixtures";
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
