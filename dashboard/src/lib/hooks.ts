"use client";

import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Poller } from "./poller";

export interface PollResult<T> {
  /** The newest answer. Kept while the next one is fetched, so the screen never blanks on a refresh. */
  data: T | null;
  /** The last failure, cleared by the next success. */
  error: unknown;
  /** True once the first request has finished, with an answer or an error. */
  loaded: boolean;
  refresh: () => void;
}

/** Fetch `fetcher` now and then every `intervalMs` while the page is visible. */
export function usePoll<T>(fetcher: (signal: AbortSignal) => Promise<T>, intervalMs: number, enabled = true): PollResult<T> {
  const [state, setState] = useState<{ data: T | null; error: unknown; loaded: boolean }>({
    data: null,
    error: null,
    loaded: false,
  });
  const latest = useRef(fetcher);
  const poller = useRef<Poller<T> | null>(null);
  useEffect(() => {
    latest.current = fetcher;
  });

  useEffect(() => {
    if (!enabled) return;
    const instance = new Poller<T>({
      fetcher: (signal) => latest.current(signal),
      intervalMs,
      onData: (data) => setState({ data, error: null, loaded: true }),
      onError: (error) => setState((previous) => ({ ...previous, error, loaded: true })),
    });
    poller.current = instance;
    instance.start();
    const onVisibility = () => instance.visibilityChanged();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      instance.stop();
      poller.current = null;
    };
  }, [intervalMs, enabled]);

  const refresh = useCallback(() => poller.current?.refresh(), []);
  return { ...state, refresh };
}

/**
 * A setting kept in localStorage. Storage can be missing or throw (private windows, blocked site
 * data), so every access is guarded and the page works without it.
 */
export function usePersistentState<T>(key: string, initial: T): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(initial);

  useEffect(() => {
    try {
      const stored = window.localStorage.getItem(key);
      // Reading storage needs the browser, so it happens after the first render.
      // eslint-disable-next-line react-hooks/set-state-in-effect
      if (stored !== null) setValue(JSON.parse(stored) as T);
    } catch {
      // keep the default
    }
  }, [key]);

  const update = useCallback(
    (next: T) => {
      setValue(next);
      try {
        window.localStorage.setItem(key, JSON.stringify(next));
      } catch {
        // not saved, still applied
      }
    },
    [key],
  );
  return [value, update];
}

/**
 * The current time, in whole steps of `stepMs`: it changes only once per step, so a component
 * that uses it re-renders at that pace and never in between.
 */
export function useNow(stepMs: number): number {
  return useSyncExternalStore(
    (onChange) => {
      const timer = setInterval(onChange, stepMs);
      return () => clearInterval(timer);
    },
    () => Math.floor(Date.now() / stepMs) * stepMs,
    () => 0,
  );
}
