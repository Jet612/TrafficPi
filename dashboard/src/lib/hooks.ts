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

export interface RequestResult<T> {
  /** The newest answer, whichever request it came from: see `answeredKey`. */
  data: T | null;
  /** The key of the request that `data` answers. It differs from the wanted key while the next answer is on its way. */
  answeredKey: string | null;
  /** The failure of the request for the wanted key; cleared by a new key or a success. */
  error: unknown;
  /** True until the request for the wanted key has been answered or has failed. */
  loading: boolean;
  refresh: () => void;
}

/**
 * Fetch when `key` changes, and again every `refreshMs` while the page is visible. An answer for a key
 * that is no longer wanted is dropped. The previous answer stays in `data` while the next is fetched,
 * so that a screen can dim instead of blanking between two days or two hours. A null key fetches
 * nothing. `delayMs` waits that long first, so that a run of quick changes makes one request.
 */
export function useRequest<T>(
  key: string | null,
  fetcher: (signal: AbortSignal) => Promise<T>,
  options: { refreshMs?: number; delayMs?: number } = {},
): RequestResult<T> {
  const { refreshMs, delayMs = 0 } = options;
  const [state, setState] = useState<{ data: T | null; answeredKey: string | null; error: unknown; failedKey: string | null }>({
    data: null,
    answeredKey: null,
    error: null,
    failedKey: null,
  });
  const [round, setRound] = useState(0);
  const latest = useRef(fetcher);
  useEffect(() => {
    latest.current = fetcher;
  });

  useEffect(() => {
    if (key === null) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      latest.current(controller.signal).then(
        (data) => {
          if (!controller.signal.aborted) setState({ data, answeredKey: key, error: null, failedKey: null });
        },
        (error: unknown) => {
          if (!controller.signal.aborted) setState((previous) => ({ ...previous, error, failedKey: key }));
        },
      );
    }, delayMs);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key, round, delayMs]);

  useEffect(() => {
    if (key === null || !refreshMs) return;
    const timer = setInterval(() => {
      if (!document.hidden) setRound((n) => n + 1);
    }, refreshMs);
    return () => clearInterval(timer);
  }, [key, refreshMs]);

  const refresh = useCallback(() => setRound((n) => n + 1), []);
  return {
    data: state.data,
    answeredKey: state.answeredKey,
    error: state.failedKey === key ? state.error : null,
    loading: key !== null && state.answeredKey !== key && state.failedKey !== key,
    refresh,
  };
}
