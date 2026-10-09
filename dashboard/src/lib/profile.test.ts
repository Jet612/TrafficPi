import { describe, expect, it } from "vitest";
import { lineOf, profileOf, rectangle, zoneOf } from "./fixtures";
import {
  MAX_ELEMENTS,
  areaPercent,
  countOf,
  displayName,
  elements,
  emptyProfile,
  findElement,
  hasErrors,
  isFull,
  newId,
  problems,
  refOf,
  sameProfile,
  sameRef,
  suggestName,
} from "./profile";

describe("the elements of a profile", () => {
  const profile = profileOf({
    lines: [lineOf("l1")],
    detection: [zoneOf("d1")],
    exclusion: [zoneOf("x1")],
  });

  it("starts from an empty profile of the current schema", () => {
    expect(emptyProfile()).toEqual({ schema_version: 2, lines: [], detection_zones: [], exclusion_zones: [], stop_signs: [] });
  });

  it("lists zones first and lines last, which is also the order they are drawn in", () => {
    expect(elements(profile).map(refOf)).toEqual([
      { kind: "detection", id: "d1" },
      { kind: "exclusion", id: "x1" },
      { kind: "line", id: "l1" },
    ]);
  });

  it("finds an element by kind and id, and the kind matters", () => {
    expect(findElement(profile, { kind: "line", id: "l1" })?.value.id).toBe("l1");
    expect(findElement(profile, { kind: "detection", id: "l1" })).toBeUndefined();
    expect(findElement(profile, { kind: "line", id: "nope" })).toBeUndefined();
  });

  it("compares references, and copes with none", () => {
    expect(sameRef({ kind: "line", id: "a" }, { kind: "line", id: "a" })).toBe(true);
    expect(sameRef({ kind: "line", id: "a" }, { kind: "detection", id: "a" })).toBe(false);
    expect(sameRef(null, { kind: "line", id: "a" })).toBe(false);
    expect(sameRef(null, undefined)).toBe(false);
    expect(sameRef(null, null)).toBe(true);
  });

  it("counts elements and knows when a kind is full", () => {
    expect(countOf(profile, "line")).toBe(1);
    expect(isFull(profile, "line")).toBe(false);
    const crowded = profileOf({ lines: Array.from({ length: MAX_ELEMENTS }, (_, i) => lineOf(`l${i}`)) });
    expect(isFull(crowded, "line")).toBe(true);
    expect(isFull(crowded, "detection")).toBe(false);
  });
});

describe("names", () => {
  it("shows the name an element has, or its kind and place in the list when it has none", () => {
    const profile = profileOf({ lines: [lineOf("l1", { name: "Main street" }), lineOf("l2")] });
    expect(displayName(profile, { kind: "line", id: "l1" })).toBe("Main street");
    expect(displayName(profile, { kind: "line", id: "l2" })).toBe("Counting line 2");
    expect(displayName(profile, { kind: "line", id: "gone" })).toBe("Counting line");
  });

  it("suggests the next free number for a kind", () => {
    expect(suggestName(emptyProfile(), "line")).toBe("Counting line 1");
    expect(suggestName(emptyProfile(), "detection")).toBe("Detection zone 1");
    expect(suggestName(emptyProfile(), "exclusion")).toBe("Exclusion zone 1");
  });

  it("numbers after the highest in use, so a deleted number is not handed out again", () => {
    const profile = profileOf({ lines: [lineOf("a", { name: "Counting line 2" })] });
    expect(suggestName(profile, "line")).toBe("Counting line 3");
    const gappy = profileOf({ lines: [lineOf("a", { name: "Counting line 1" }), lineOf("b", { name: "Counting line 3" })] });
    expect(suggestName(gappy, "line")).toBe("Counting line 4");
  });

  it("does not let the names people choose upset the numbering", () => {
    const profile = profileOf({ lines: [lineOf("a", { name: "Main street" }), lineOf("b", { name: "Side street" })] });
    expect(suggestName(profile, "line")).toBe("Counting line 3");
  });
});

describe("new ids", () => {
  it("start with the kind and are never one already in use", () => {
    const profile = profileOf({ lines: [lineOf("line-aaaaaa")] });
    const wanted = ["aaaaaa", "aaaaaa", "bbbbbb"];
    let calls = 0;
    expect(newId(profile, "line", () => wanted[calls++]!)).toBe("line-bbbbbb");
    expect(calls).toBe(3);
    expect(newId(emptyProfile(), "detection", () => "123456")).toBe("area-123456");
    expect(newId(emptyProfile(), "exclusion", () => "123456")).toBe("ignore-123456");
  });

  it("are random hex by default", () => {
    expect(newId(emptyProfile(), "line")).toMatch(/^line-[0-9a-f]{6}$/);
  });
});

describe("problems", () => {
  it("finds nothing wrong with a sensible profile", () => {
    const profile = profileOf({
      lines: [lineOf("l1")],
      detection: [zoneOf("d1", rectangle(0.1, 0.1, 0.9, 0.9))],
      exclusion: [zoneOf("x1", rectangle(0.05, 0.05, 0.15, 0.15))],
    });
    expect(problems(profile)).toEqual([]);
    expect(hasErrors(problems(profile))).toBe(false);
  });

  it("refuses a line whose ends are in the same place", () => {
    const found = problems(profileOf({ lines: [lineOf("l1", { a: [0.5, 0.5], b: [0.5, 0.5] })] }));
    expect(found).toEqual([
      { level: "error", ref: { kind: "line", id: "l1" }, message: "Counting line 1: the two ends are in the same place" },
    ]);
  });

  it("refuses a line that counts no kind of vehicle, but accepts null, which means every kind", () => {
    const none = problems(profileOf({ lines: [lineOf("l1", { classes: [] })] }));
    expect(none.map((p) => p.message)).toEqual(["Counting line 1: choose at least one kind of vehicle to count"]);
    expect(problems(profileOf({ lines: [lineOf("l1", { classes: null })] }))).toEqual([]);
  });

  it("explains an outline the server would refuse, naming the zone", () => {
    const bowTie = [[0.2, 0.2], [0.8, 0.8], [0.8, 0.2], [0.2, 0.8]] as [number, number][];
    const found = problems(profileOf({ detection: [zoneOf("d1", bowTie, "The road")] }));
    expect(found).toHaveLength(1);
    expect(found[0]).toMatchObject({ level: "error", ref: { kind: "detection", id: "d1" } });
    expect(found[0]!.message).toMatch(/^The road: the outline crosses itself/);
    const tiny = problems(profileOf({ exclusion: [zoneOf("x1", rectangle(0.5, 0.5, 0.5005, 0.5005))] }));
    expect(tiny[0]!.message).toMatch(/^Exclusion zone 1: the zone is too small/);
  });

  it("warns about a line that lies where vehicles are ignored", () => {
    const profile = profileOf({
      lines: [lineOf("l1", { name: "Gate" })],
      exclusion: [zoneOf("x1", rectangle(0.3, 0.3, 0.7, 0.7), "Parking")],
    });
    const found = problems(profile);
    expect(found).toHaveLength(1);
    expect(found[0]).toMatchObject({ level: "warning", ref: { kind: "line", id: "l1" } });
    expect(found[0]!.message).toBe("Gate lies inside Parking, where vehicles are ignored, so it will not count anything");
    expect(hasErrors(found)).toBe(false);
  });

  it("warns about a line outside every detection zone, once there are any", () => {
    const outside = profileOf({ lines: [lineOf("l1")], detection: [zoneOf("d1", rectangle(0.1, 0.8, 0.9, 0.95))] });
    expect(problems(outside).map((p) => p.message)).toEqual([
      "Counting line 1 lies outside the detection zones, so it will not count anything",
    ]);
    const inside = profileOf({ lines: [lineOf("l1")], detection: [zoneOf("d1", rectangle(0.1, 0.4, 0.9, 0.6))] });
    expect(problems(inside)).toEqual([]);
    // With no detection zone at all the whole picture counts.
    expect(problems(profileOf({ lines: [lineOf("l1")] }))).toEqual([]);
  });

  it("says only that the exclusion wins when a line is in both kinds of zone", () => {
    const profile = profileOf({
      lines: [lineOf("l1")],
      detection: [zoneOf("d1", rectangle(0, 0, 1, 1))],
      exclusion: [zoneOf("x1", rectangle(0.3, 0.3, 0.7, 0.7))],
    });
    expect(problems(profile).map((p) => p.message)).toEqual([
      expect.stringContaining("lies inside Exclusion zone 1"),
    ]);
  });
});

describe("small helpers", () => {
  it("gives a zone's area as a whole percent of the picture", () => {
    expect(areaPercent(zoneOf("d", rectangle(0, 0, 0.5, 0.5)))).toBe(25);
    expect(areaPercent(zoneOf("d", rectangle(0.1, 0.1, 0.1 + 0.014, 0.1 + 0.014)))).toBe(1); // never 0 for a real zone
    expect(areaPercent(zoneOf("d", rectangle(0.5, 0.5, 0.5005, 0.5005)))).toBe(0); // too small to exist
  });

  it("tells whether two profiles say the same thing", () => {
    const a = profileOf({ lines: [lineOf("l1")] });
    expect(sameProfile(a, structuredClone(a))).toBe(true);
    expect(sameProfile(a, profileOf({ lines: [lineOf("l1", { a: [0.21, 0.5] })] }))).toBe(false);
  });

  it("does not mind the order of the keys, which differs between the server and a profile built here", () => {
    const reordered = {
      exclusion_zones: [],
      lines: [{ persistence: null, margin: null, point: "ground", classes: ["car", "truck", "bus", "motorcycle"], b: [0.8, 0.5], a: [0.2, 0.5], name: "", id: "l1" }],
      detection_zones: [],
      stop_signs: [],
      schema_version: 2,
    } as unknown as Parameters<typeof sameProfile>[0];
    expect(sameProfile(profileOf({ lines: [lineOf("l1")] }), reordered)).toBe(true);
    // The order of a list is part of what it says, though.
    const twoLines = profileOf({ lines: [lineOf("a"), lineOf("b")] });
    expect(sameProfile(twoLines, profileOf({ lines: [lineOf("b"), lineOf("a")] }))).toBe(false);
  });
});
