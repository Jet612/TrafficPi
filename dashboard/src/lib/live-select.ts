import type { LiveSnapshot } from "./live";
import type { LiveLine } from "./types";

/** The counting lines as the vision service reports them, for a component that needs where they are. */
export const selectLines = (snapshot: LiveSnapshot): LiveLine[] | undefined => snapshot.state?.lines;

/** Two lists of lines are the same when the lines are in the same places: the socket sends a new list ten times a second. */
export const sameLines = (a: LiveLine[] | undefined, b: LiveLine[] | undefined): boolean =>
  a === b ||
  (a !== undefined &&
    b !== undefined &&
    a.length === b.length &&
    a.every((line, i) => line.id === b[i]?.id && line.a[0] === b[i]?.a[0] && line.a[1] === b[i]?.a[1] && line.b[0] === b[i]?.b[0] && line.b[1] === b[i]?.b[1]));
