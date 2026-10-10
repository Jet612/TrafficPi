import { describe, expect, it } from "vitest";
import {
  addCalibration,
  addCheck,
  canAddCorner,
  canRemoveCorner,
  cornerCount,
  deleteElement,
  insertCorner,
  moveElement,
  movePoint,
  pointsOf,
  removeCorner,
  renameElement,
  setCalibrationSize,
  setCheckLength,
  splitLongestEdge,
} from "./editor";
import { lineOf, profileOf } from "./fixtures";
import { displayName, elements, findElement, hasErrors, isFull, problems, emptyProfile } from "./profile";
import { editorReducer, initialEditorState } from "./editor-state";
import type { Profile } from "./types";

const fixedId = () => "abc123";
const CAL = { kind: "calibration", id: "calibration" } as const;
const withCalibration = (): Profile => addCalibration(emptyProfile())!.profile;
const withCheck = (): Profile => addCheck(withCalibration(), fixedId)!.profile;
const CHECK = { kind: "check", id: "check-abc123" } as const;

describe("adding the speed calibration", () => {
  it("lays a rectangle on the picture and selects it", () => {
    const added = addCalibration(emptyProfile())!;
    expect(added.ref).toEqual(CAL);
    expect(added.profile.calibration).not.toBeNull();
    expect(added.profile.calibration!.corners).toHaveLength(4);
  });

  it("refuses a second one: a camera has one road", () => {
    expect(addCalibration(withCalibration())).toBeNull();
  });

  it("does not touch the profile it was given", () => {
    const before = emptyProfile();
    addCalibration(before);
    expect(before.calibration).toBeNull();
  });

  it("is an element of the profile with a name of its own, listed after the zones and before the stop signs", () => {
    const profile = { ...withCalibration(), lines: [lineOf("l1")] };
    expect(elements(profile).map((e) => e.kind)).toEqual(["calibration", "line"]);
    expect(displayName(profile, CAL)).toBe("Speed calibration");
    expect(findElement(profile, CAL)?.value.id).toBe("calibration");
  });

  it("is full with one", () => {
    expect(isFull(emptyProfile(), "calibration")).toBe(false);
    expect(isFull(withCalibration(), "calibration")).toBe(true);
  });
});

describe("its corners", () => {
  it("are four points, which move one at a time and are kept inside the picture", () => {
    const profile = withCalibration();
    expect(pointsOf(profile, CAL)).toEqual(profile.calibration!.corners);
    const moved = movePoint(profile, CAL, 2, [0.62, 0.41]);
    expect(moved.calibration!.corners[2]).toEqual([0.62, 0.41]);
    expect(moved.calibration!.corners[1]).toEqual(profile.calibration!.corners[1]);
    expect(movePoint(profile, CAL, 0, [3, -2]).calibration!.corners[0]).toEqual([1, 0]);
  });

  it("give back the same profile when nothing changes", () => {
    const profile = withCalibration();
    expect(movePoint(profile, CAL, 1, profile.calibration!.corners[1]!)).toBe(profile);
    expect(movePoint(profile, CAL, 4, [0.5, 0.5])).toBe(profile); // there is no fifth
    expect(movePoint(emptyProfile(), CAL, 0, [0.5, 0.5])).toEqual(emptyProfile());
  });

  it("cannot be added to or taken away: a rectangle has four", () => {
    const profile = withCalibration();
    expect(cornerCount(profile, CAL)).toBe(0);
    expect(canAddCorner(profile, CAL)).toBe(false);
    expect(canRemoveCorner(profile, CAL, 0)).toBe(false);
    expect(insertCorner(profile, CAL, 0, [0.5, 0.5])).toBe(profile);
    expect(removeCorner(profile, CAL, 0)).toBe(profile);
    expect(splitLongestEdge(profile, CAL)).toBeNull();
  });

  it("move together with the rectangle, which stops at the edge of the picture with its shape", () => {
    const profile = withCalibration();
    const moved = moveElement(profile, CAL, 0.05, -0.1);
    moved.calibration!.corners.forEach((corner, i) => {
      expect(corner[0]).toBeCloseTo(profile.calibration!.corners[i]![0] + 0.05);
      expect(corner[1]).toBeCloseTo(profile.calibration!.corners[i]![1] - 0.1);
    });
    const pushed = moveElement(profile, CAL, 5, 0);
    expect(Math.max(...pushed.calibration!.corners.map((c) => c[0]))).toBe(1);
    expect(pushed.calibration!.corners[1]![0] - pushed.calibration!.corners[0]![0]).toBeCloseTo(0.4); // the shape is kept
  });

  it("leave the known distances where they are when the rectangle moves", () => {
    const profile = withCheck();
    const moved = moveElement(profile, CAL, 0.05, 0);
    expect(moved.calibration!.checks).toEqual(profile.calibration!.checks);
  });
});

describe("the size of the rectangle", () => {
  it("is set in metres, to the thousandth", () => {
    const profile = withCalibration();
    expect(setCalibrationSize(profile, "width_m", 3.5).calibration!.width_m).toBe(3.5);
    expect(setCalibrationSize(profile, "length_m", 12.3456).calibration!.length_m).toBe(12.346);
  });

  it("keeps the old size when the new one is out of range or not a number", () => {
    const profile = withCalibration();
    expect(setCalibrationSize(profile, "width_m", 0.1)).toBe(profile);
    expect(setCalibrationSize(profile, "width_m", 101)).toBe(profile);
    expect(setCalibrationSize(profile, "length_m", 0.9)).toBe(profile);
    expect(setCalibrationSize(profile, "length_m", 501)).toBe(profile);
    expect(setCalibrationSize(profile, "length_m", Number.NaN)).toBe(profile);
  });

  it("gives back the same profile when it is the size already", () => {
    const profile = withCalibration();
    expect(setCalibrationSize(profile, "width_m", profile.calibration!.width_m)).toBe(profile);
    expect(setCalibrationSize(emptyProfile(), "width_m", 5)).toEqual(emptyProfile());
  });
});

describe("a known distance", () => {
  it("needs a calibration to test", () => {
    expect(addCheck(emptyProfile(), fixedId)).toBeNull();
  });

  it("is added along the road with a name and an id, and selected", () => {
    const added = addCheck(withCalibration(), fixedId)!;
    expect(added.ref).toEqual(CHECK);
    expect(added.profile.calibration!.checks).toHaveLength(1);
    expect(added.profile.calibration!.checks[0]).toMatchObject({ id: "check-abc123", name: "Known distance 1", length_m: 15 });
    expect(displayName(added.profile, CHECK)).toBe("Known distance 1");
  });

  it("is limited to eight, the most the server takes", () => {
    let profile = withCalibration();
    let n = 0;
    for (let i = 0; i < 8; i += 1) profile = addCheck(profile, () => `c${(n += 1)}`)!.profile;
    expect(profile.calibration!.checks).toHaveLength(8);
    expect(addCheck(profile, fixedId)).toBeNull();
    expect(isFull(profile, "check")).toBe(true);
  });

  it("is moved by its ends and as a whole, and has a length and a name", () => {
    const profile = withCheck();
    expect(pointsOf(profile, CHECK)).toHaveLength(2);
    const end = movePoint(profile, CHECK, 1, [0.55, 0.4]);
    expect(end.calibration!.checks[0]!.b).toEqual([0.55, 0.4]);
    expect(end.calibration!.checks[0]!.a).toEqual(profile.calibration!.checks[0]!.a);
    expect(movePoint(profile, CHECK, 2, [0.1, 0.1])).toBe(profile);
    const shifted = moveElement(profile, CHECK, 0.1, 0);
    expect(shifted.calibration!.checks[0]!.a[0]).toBeCloseTo(profile.calibration!.checks[0]!.a[0] + 0.1);
    expect(setCheckLength(profile, "check-abc123", 21.2).calibration!.checks[0]!.length_m).toBe(21.2);
    expect(setCheckLength(profile, "check-abc123", 0.1)).toBe(profile);
    expect(setCheckLength(profile, "check-abc123", 600)).toBe(profile);
    expect(renameElement(profile, CHECK, "Driveway to the pole").calibration!.checks[0]!.name).toBe("Driveway to the pole");
  });

  it("is not an outline: it has no corners to add or take away", () => {
    const profile = withCheck();
    expect(cornerCount(profile, CHECK)).toBe(0);
    expect(insertCorner(profile, CHECK, 0, [0.5, 0.5])).toBe(profile);
    expect(removeCorner(profile, CHECK, 0)).toBe(profile);
  });

  it("goes when deleted, and the rectangle stays", () => {
    const profile = withCheck();
    const gone = deleteElement(profile, CHECK);
    expect(gone.calibration!.checks).toEqual([]);
    expect(gone.calibration!.corners).toEqual(profile.calibration!.corners);
    expect(deleteElement(gone, CHECK)).toBe(gone);
  });
});

describe("the calibration has no name to change, and deleting it takes its known distances with it", () => {
  it("ignores a rename", () => {
    const profile = withCalibration();
    expect(renameElement(profile, CAL, "My road")).toBe(profile);
  });

  it("is deleted whole", () => {
    const gone = deleteElement(withCheck(), CAL);
    expect(gone.calibration).toBeNull();
    expect(deleteElement(gone, CAL)).toBe(gone);
  });

  it("can be undone, known distances and all", () => {
    let state = initialEditorState(withCheck());
    state = editorReducer(state, { type: "change", profile: deleteElement(state.present, CAL) });
    expect(state.present.calibration).toBeNull();
    state = editorReducer(state, { type: "undo" });
    expect(state.present.calibration!.checks).toHaveLength(1);
  });

  it("drops the selection when its element is deleted", () => {
    let state = initialEditorState(withCheck());
    state = editorReducer(state, { type: "select", ref: CHECK });
    expect(state.selection).toEqual(CHECK);
    state = editorReducer(state, { type: "change", profile: deleteElement(state.present, CAL) });
    expect(state.selection).toBeNull();
  });
});

describe("what the editor says is wrong", () => {
  it("is nothing for a fresh rectangle and a fresh known distance", () => {
    expect(problems(withCheck())).toEqual([]);
  });

  it("is an error, against the calibration, when the corners are dragged into a bow tie", () => {
    const profile = movePoint(movePoint(withCalibration(), CAL, 1, [0.6, 0.5]), CAL, 2, [0.7, 0.85]);
    const found = problems(profile);
    expect(found).toHaveLength(1);
    expect(found[0]).toMatchObject({ level: "error", ref: CAL });
    expect(found[0]!.message).toBe("Speed calibration: the corners must go round the rectangle one after the other, without crossing");
    expect(hasErrors(found)).toBe(true);
  });

  it("is an error, against the known distance, when its ends are in one place", () => {
    const profile = withCheck();
    const flat = movePoint(profile, CHECK, 1, profile.calibration!.checks[0]!.a);
    expect(problems(flat)).toEqual([
      { level: "error", ref: CHECK, message: "Known distance 1: the two ends are in the same place" },
    ]);
  });

  it("does not blame a profile that has no calibration", () => {
    expect(problems(profileOf({ lines: [lineOf("l1")] }))).toEqual([]);
  });
});
