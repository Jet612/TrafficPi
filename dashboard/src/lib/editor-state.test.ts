import { describe, expect, it } from "vitest";
import { addLine, deleteElement, movePoint } from "./editor";
import {
  HISTORY_LIMIT,
  canRedo,
  canUndo,
  editorReducer,
  initialEditorState,
  type EditorAction,
  type EditorState,
} from "./editor-state";
import { lineOf, profileOf, zoneOf } from "./fixtures";
import { emptyProfile } from "./profile";
import type { Profile } from "./types";

const run = (state: EditorState, ...actions: EditorAction[]): EditorState => actions.reduce(editorReducer, state);

const ref = { kind: "line", id: "l1" } as const;
const start = profileOf({ lines: [lineOf("l1")] });
const moved = (x: number): Profile => movePoint(start, ref, 0, [x, 0.5]);

describe("steps", () => {
  it("starts with nothing to undo or redo", () => {
    const state = initialEditorState(start);
    expect([canUndo(state), canRedo(state)]).toEqual([false, false]);
    expect(state.selection).toBeNull();
  });

  it("makes one step of each change, and undo and redo walk along them", () => {
    let state = run(initialEditorState(start), { type: "change", profile: moved(0.3) }, { type: "change", profile: moved(0.4) });
    expect(state.present).toEqual(moved(0.4));
    expect(state.past).toHaveLength(2);

    state = run(state, { type: "undo" });
    expect(state.present).toEqual(moved(0.3));
    state = run(state, { type: "undo" });
    expect(state.present).toEqual(start);
    expect(canUndo(state)).toBe(false);
    expect(run(state, { type: "undo" })).toBe(state); // nothing left: no new state

    state = run(state, { type: "redo" }, { type: "redo" });
    expect(state.present).toEqual(moved(0.4));
    expect(canRedo(state)).toBe(false);
    expect(run(state, { type: "redo" })).toBe(state);
  });

  it("forgets what could be redone once something new is done", () => {
    let state = run(initialEditorState(start), { type: "change", profile: moved(0.3) }, { type: "undo" });
    expect(canRedo(state)).toBe(true);
    state = run(state, { type: "change", profile: moved(0.6) });
    expect(canRedo(state)).toBe(false);
  });

  it("does not count a change that changes nothing", () => {
    const state = initialEditorState(start);
    expect(run(state, { type: "change", profile: structuredClone(start) })).toBe(state);
  });

  it("keeps only the most recent steps", () => {
    let state = initialEditorState(start);
    for (let i = 1; i <= HISTORY_LIMIT + 20; i += 1) {
      state = run(state, { type: "change", profile: moved(i / 1000) });
    }
    expect(state.past).toHaveLength(HISTORY_LIMIT);
    // `past` holds the profile before each change: the start and steps 1 to 19 were dropped.
    expect(state.past[0]).toEqual(moved(20 / 1000));
    expect(state.past.at(-1)).toEqual(moved((HISTORY_LIMIT + 19) / 1000));
  });
});

describe("gestures", () => {
  it("makes one step of a whole drag", () => {
    let state = initialEditorState(start);
    for (const x of [0.25, 0.3, 0.35, 0.4]) state = run(state, { type: "live", profile: moved(x) });
    expect(state.present).toEqual(moved(0.4)); // shown at once
    expect(state.past).toHaveLength(0); // but not a step yet
    expect(canUndo(state)).toBe(true); // although it can be undone

    state = run(state, { type: "settle" });
    expect(state.past).toEqual([start]);
    expect(state.anchor).toBeNull();
    expect(run(state, { type: "undo" }).present).toEqual(start);
  });

  it("makes no step of a drag that ends where it began", () => {
    const state = run(initialEditorState(start), { type: "live", profile: moved(0.4) }, { type: "live", profile: start }, { type: "settle" });
    expect(state.past).toEqual([]);
    expect(state.anchor).toBeNull();
  });

  it("settles a gesture that is still going before undoing, so that undo goes back to where it began", () => {
    const state = run(initialEditorState(start), { type: "live", profile: moved(0.4) }, { type: "undo" });
    expect(state.present).toEqual(start);
    expect(state.past).toEqual([]);
    expect(state.future).toEqual([moved(0.4)]);
  });

  it("settles a gesture before a change", () => {
    const state = run(initialEditorState(start), { type: "live", profile: moved(0.4) }, { type: "change", profile: deleteElement(moved(0.4), ref) });
    expect(state.past).toEqual([start, moved(0.4)]);
  });

  it("ignores a live update that is the profile it already shows", () => {
    const state = initialEditorState(start);
    expect(run(state, { type: "live", profile: state.present })).toBe(state);
  });
});

describe("selection", () => {
  it("selects what exists and ignores what does not", () => {
    const state = initialEditorState(start);
    expect(run(state, { type: "select", ref }).selection).toEqual(ref);
    expect(run(state, { type: "select", ref: { kind: "line", id: "gone" } }).selection).toBeNull();
    expect(run(state, { type: "select", ref }, { type: "select", ref: null }).selection).toBeNull();
  });

  it("is dropped when its element is deleted, and does not come back by itself on undo", () => {
    let state = run(initialEditorState(start), { type: "select", ref }, { type: "change", profile: deleteElement(start, ref) });
    expect(state.selection).toBeNull();
    state = run(state, { type: "undo" });
    expect(state.present).toEqual(start);
    expect(state.selection).toBeNull();
  });

  it("is dropped when undo removes the element that was just added", () => {
    const added = addLine(emptyProfile(), () => "abc123")!;
    let state = run(initialEditorState(emptyProfile()), { type: "change", profile: added.profile }, { type: "select", ref: added.ref });
    expect(state.selection).toEqual(added.ref);
    state = run(state, { type: "undo" });
    expect(state.selection).toBeNull();
  });

  it("survives a reload of the saved profile when the element is still there", () => {
    const state = run(initialEditorState(start), { type: "select", ref }, { type: "change", profile: moved(0.4) });
    const reloaded = run(state, { type: "load", profile: moved(0.4) });
    expect(reloaded.selection).toEqual(ref);
    expect(reloaded.past).toEqual([]); // saving starts a clean history
    const elsewhere = run(state, { type: "load", profile: profileOf({ detection: [zoneOf("d1")] }) });
    expect(elsewhere.selection).toBeNull();
  });
});
