import { describe, expect, it } from "vitest";
import { lineOf, profileOf, rectangle, stopOf, zoneOf } from "./fixtures";
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
    expect(emptyProfile()).toEqual({ schema_version: 3, lines: [], detection_zones: [], exclusion_zones: [], stop_signs: [], calibration: null });
  });

  it("puts stop signs between the zones and the lines", () => {
    const withStop = profileOf({ lines: [lineOf("l1")], detection: [zoneOf("d1")], exclusion: [zoneOf("x1")], stops: [stopOf("s1")] });
    expect(elements(withStop).map(refOf)).toEqual([
      { kind: "detection", id: "d1" },
      { kind: "exclusion", id: "x1" },
      { kind: "stop", id: "s1" },
      { kind: "line", id: "l1" },
    ]);
    expect(findElement(withStop, { kind: "stop", id: "s1" })?.value.id).toBe("s1");
    expect(findElement(withStop, { kind: "line", id: "s1" })).toBeUndefined();
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
      calibration: null,
      schema_version: 3,
    } as unknown as Parameters<typeof sameProfile>[0];
    expect(sameProfile(profileOf({ lines: [lineOf("l1")] }), reordered)).toBe(true);
    // The order of a list is part of what it says, though.
    const twoLines = profileOf({ lines: [lineOf("a"), lineOf("b")] });
    expect(sameProfile(twoLines, profileOf({ lines: [lineOf("b"), lineOf("a")] }))).toBe(false);
  });
});

describe("stop signs", () => {
  const sign = (over = {}) => profileOf({ stops: [stopOf("s1", over)] });
  const messages = (profile = sign()) => problems(profile).map((p) => `${p.level}: ${p.message}`);

  it("are named like the other elements, and numbered after the highest in use", () => {
    expect(displayName(sign(), { kind: "stop", id: "s1" })).toBe("Stop sign 1");
    expect(displayName(sign({ name: "Elm Street" }), { kind: "stop", id: "s1" })).toBe("Elm Street");
    expect(suggestName(sign({ name: "Stop sign 4" }), "stop")).toBe("Stop sign 5");
    expect(newId(sign(), "stop", () => "abc123")).toBe("stop-abc123");
    expect(countOf(sign(), "stop")).toBe(1);
    expect(isFull(profileOf({ stops: Array.from({ length: MAX_ELEMENTS }, (_, i) => stopOf(`s${i}`)) }), "stop")).toBe(true);
  });

  it("have nothing wrong when the zone is behind the line, close to it, and the numbers are sensible", () => {
    expect(messages()).toEqual([]);
    expect(messages(sign({ direction: -1, approach: rectangle(0.5, 0.45, 0.85, 0.75) }))).toEqual([]);
  });

  it("are refused when a part makes no sense", () => {
    expect(messages(sign({ a: [0.5, 0.5], b: [0.5, 0.5] }))[0]).toBe("error: Stop sign 1: the two ends of the stop line are in the same place");
    expect(messages(sign({ approach: [[0.1, 0.1], [0.5, 0.5], [0.1, 0.5], [0.5, 0.1]] }))[0]).toMatch(/^error: Stop sign 1: the approach zone: /);
    expect(messages(sign({ classes: [] }))).toEqual(["error: Stop sign 1: choose at least one kind of vehicle that has to stop"]);
  });

  it("are refused when a number is outside what the server takes", () => {
    expect(messages(sign({ hold_s: 0.1 }))).toEqual(["error: Stop sign 1: how long it must stand still must be between 0.2 and 10"]);
    expect(messages(sign({ still_radius: 0.9 }))[0]).toContain("how little it may move meanwhile");
    expect(messages(sign({ roll_speed: 0 }))[0]).toContain("the speed below which it is rolling");
    expect(messages(sign({ min_observed_s: Number.NaN }))[0]).toContain("how long it must be watched");
    expect(hasErrors(problems(sign({ hold_s: 11 })))).toBe(true);
  });

  it("warn when the approach zone is on the side the arrow points to, so that vehicles are judged after the line", () => {
    const [warning] = messages(sign({ direction: -1 })); // the same drawing, the other way through
    expect(warning).toMatch(/^warning: Stop sign 1: the approach zone is on the far side of the stop line/);
    expect(hasErrors(problems(sign({ direction: -1 })))).toBe(false); // worth knowing; not a reason to refuse
  });

  it("warn when the stop line is far from the approach zone", () => {
    const far = sign({ a: [0.9, 0.75], b: [0.9, 0.45] });
    expect(messages(far)).toEqual([expect.stringMatching(/^warning: Stop sign 1: the stop line is a long way from the approach zone/)]);
  });

  it("warn when the approach zone lies where vehicles are ignored or not looked for", () => {
    const parked = profileOf({ stops: [stopOf("s1")], exclusion: [zoneOf("x1", rectangle(0.1, 0.4, 0.6, 0.8), "Parked cars")] });
    expect(messages(parked)).toEqual(["warning: Stop sign 1: the approach zone lies inside Parked cars, where vehicles are ignored, so it will not judge anything"]);
    const elsewhere = profileOf({ stops: [stopOf("s1")], detection: [zoneOf("d1", rectangle(0.6, 0.1, 0.95, 0.4))] });
    expect(messages(elsewhere)).toEqual(["warning: Stop sign 1: the approach zone lies outside the detection zones, so it will not judge anything"]);
    const inside = profileOf({ stops: [stopOf("s1")], detection: [zoneOf("d1", rectangle(0.05, 0.3, 0.95, 0.9))] });
    expect(messages(inside)).toEqual([]);
  });

  it("do not pile geometry warnings on top of an error in the geometry", () => {
    const flat = sign({ a: [0.5, 0.5], b: [0.5, 0.5], direction: -1 });
    expect(messages(flat).every((m) => m.startsWith("error:"))).toBe(true);
  });
});
