"use client";

import { createContext, useCallback, useContext, useEffect, useId, useMemo, useRef } from "react";

export const LEAVE_MESSAGE = "This page has changes that are not saved. Leave without saving them?";

interface Unsaved {
  /** A part of a page says whether it holds changes that leaving would lose (each part by its own `id`). */
  report: (id: string, dirty: boolean) => void;
  /** Whether it does, right now (read when a link is followed, so it needs no re-render). */
  isDirty: () => boolean;
  /** Ask whether to leave, if there is anything to lose. True when it is fine to go. */
  confirmLeave: () => boolean;
}

const UnsavedContext = createContext<Unsaved | null>(null);

/**
 * Pages are separate routes now, so following a link unmounts the page and, with it, an edit in
 * progress. This lets a page say it has unsaved work, and the navigation ask before throwing it
 * away. (Closing or reloading the tab is asked by the browser itself: see `useUnsavedWarning`.)
 */
export function UnsavedProvider({ children }: { children: React.ReactNode }) {
  // A page can have more than one part that holds changes (the editor and the settings on Setup),
  // so each reports for itself and leaving is a loss if any of them does.
  const dirty = useRef(new Set<string>());
  const report = useCallback((id: string, value: boolean) => {
    if (value) dirty.current.add(id);
    else dirty.current.delete(id);
  }, []);
  const isDirty = useCallback(() => dirty.current.size > 0, []);
  const confirmLeave = useCallback(() => dirty.current.size === 0 || window.confirm(LEAVE_MESSAGE), []);
  const value = useMemo(() => ({ report, isDirty, confirmLeave }), [report, isDirty, confirmLeave]);
  return <UnsavedContext.Provider value={value}>{children}</UnsavedContext.Provider>;
}

export function useUnsaved(): Unsaved {
  const value = useContext(UnsavedContext);
  if (!value) throw new Error("useUnsaved needs an UnsavedProvider above it");
  return value;
}

/** Call from a page, or a part of one, with whether it has unsaved changes. */
export function useUnsavedWarning(dirty: boolean): void {
  const { report } = useUnsaved();
  const id = useId();
  useEffect(() => {
    report(id, dirty);
    return () => report(id, false);
  }, [dirty, id, report]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (event: BeforeUnloadEvent) => {
      event.preventDefault(); // the browser shows its own wording
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);
}
