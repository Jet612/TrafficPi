/**
 * The zone editor's state: the profile being edited, the undo history and what is selected.
 *
 * A drag (or a run of arrow-key presses) changes the profile many times but is one step to undo:
 * `live` updates the picture as it goes and remembers where the gesture started; `settle` closes
 * it. A button press is a single `change`.
 */
import { findElement, sameProfile, type ElementRef } from "./profile";
import type { Profile } from "./types";

/** How many steps can be undone. A step is a whole drag, so this is generous. */
export const HISTORY_LIMIT = 100;

export interface EditorState {
  past: Profile[];
  present: Profile;
  future: Profile[];
  /** The profile as it was when the gesture in progress began; null between gestures. */
  anchor: Profile | null;
  selection: ElementRef | null;
}

export type EditorAction =
  /** A fresh start (the profile was loaded or saved): nothing to undo. */
  | { type: "load"; profile: Profile }
  /** One undoable step, such as a button press or an import. */
  | { type: "change"; profile: Profile }
  /** Part of a longer gesture: shown at once, undone together with the rest of it. */
  | { type: "live"; profile: Profile }
  /** The gesture is over. */
  | { type: "settle" }
  | { type: "undo" }
  | { type: "redo" }
  | { type: "select"; ref: ElementRef | null };

export function initialEditorState(profile: Profile): EditorState {
  return { past: [], present: profile, future: [], anchor: null, selection: null };
}

export const canUndo = (state: EditorState): boolean => state.past.length > 0 || state.anchor !== null;
export const canRedo = (state: EditorState): boolean => state.future.length > 0;

const exists = (profile: Profile, ref: ElementRef | null): boolean => ref !== null && findElement(profile, ref) !== undefined;

/** Drop the selection when its element is gone (deleted, or undone away). */
const withValidSelection = (state: EditorState): EditorState =>
  state.selection === null || exists(state.present, state.selection) ? state : { ...state, selection: null };

function push(past: readonly Profile[], entry: Profile): Profile[] {
  const next = [...past, entry];
  return next.length > HISTORY_LIMIT ? next.slice(next.length - HISTORY_LIMIT) : next;
}

/** End the gesture in progress, if any: it becomes one step in the history, if it changed anything. */
function settled(state: EditorState): EditorState {
  const { anchor } = state;
  if (anchor === null) return state;
  if (sameProfile(anchor, state.present)) return { ...state, anchor: null };
  return { ...state, past: push(state.past, anchor), future: [], anchor: null };
}

export function editorReducer(state: EditorState, action: EditorAction): EditorState {
  switch (action.type) {
    case "load":
      return {
        ...initialEditorState(action.profile),
        selection: exists(action.profile, state.selection) ? state.selection : null,
      };

    case "change": {
      const base = settled(state);
      if (sameProfile(base.present, action.profile)) return base;
      return withValidSelection({ ...base, past: push(base.past, base.present), present: action.profile, future: [] });
    }

    case "live":
      if (action.profile === state.present) return state;
      return withValidSelection({ ...state, anchor: state.anchor ?? state.present, present: action.profile });

    case "settle":
      return settled(state);

    case "undo": {
      const base = settled(state);
      const previous = base.past.at(-1);
      if (previous === undefined) return base;
      return withValidSelection({
        ...base,
        past: base.past.slice(0, -1),
        present: previous,
        future: [base.present, ...base.future],
      });
    }

    case "redo": {
      const base = settled(state);
      const [next, ...rest] = base.future;
      if (next === undefined) return base;
      return withValidSelection({ ...base, past: push(base.past, base.present), present: next, future: rest });
    }

    case "select":
      return { ...state, selection: exists(state.present, action.ref) ? action.ref : null };
  }
}
