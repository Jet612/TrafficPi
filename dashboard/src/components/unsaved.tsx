"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef } from "react";

export const LEAVE_MESSAGE = "This page has changes that are not saved. Leave without saving them?";

interface Unsaved {
  /** A page says whether it holds changes that leaving would lose. */
  report: (dirty: boolean) => void;
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
  const dirty = useRef(false);
  const report = useCallback((value: boolean) => {
    dirty.current = value;
  }, []);
  const isDirty = useCallback(() => dirty.current, []);
  const confirmLeave = useCallback(() => !dirty.current || window.confirm(LEAVE_MESSAGE), []);
  const value = useMemo(() => ({ report, isDirty, confirmLeave }), [report, isDirty, confirmLeave]);
  return <UnsavedContext.Provider value={value}>{children}</UnsavedContext.Provider>;
}

export function useUnsaved(): Unsaved {
  const value = useContext(UnsavedContext);
  if (!value) throw new Error("useUnsaved needs an UnsavedProvider above it");
  return value;
}

/** Call from a page with whether it has unsaved changes. */
export function useUnsavedWarning(dirty: boolean): void {
  const { report } = useUnsaved();
  useEffect(() => {
    report(dirty);
    return () => report(false);
  }, [dirty, report]);
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
