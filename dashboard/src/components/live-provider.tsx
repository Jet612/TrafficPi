"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { LiveClient, type LiveSnapshot } from "@/lib/live";

const LiveContext = createContext<LiveClient | null>(null);

function socketUrl(): string {
  const scheme = window.location.protocol === "https:" ? "wss:" : "ws:";
  return `${scheme}//${window.location.host}/api/live`;
}

/**
 * Owns the one live socket for the page; it is closed while the page is hidden. `onRefused` is
 * called when the server turns the socket away (usually: the session ended) and must be a stable
 * function, because the socket is created once.
 */
export function LiveProvider({ onRefused, children }: { onRefused: () => void; children: React.ReactNode }) {
  const [client] = useState(() => new LiveClient({ url: socketUrl, onRefused }));

  useEffect(() => {
    const pauseWhenHidden = () => client.setPaused(document.hidden);
    pauseWhenHidden();
    client.start();
    document.addEventListener("visibilitychange", pauseWhenHidden);
    return () => {
      document.removeEventListener("visibilitychange", pauseWhenHidden);
      client.stop();
    };
  }, [client]);

  return <LiveContext.Provider value={client}>{children}</LiveContext.Provider>;
}

export function useLiveClient(): LiveClient {
  const client = useContext(LiveContext);
  if (!client) throw new Error("useLiveClient needs a LiveProvider above it");
  return client;
}

/**
 * Read part of the live state. The socket delivers about ten messages a second; a component only
 * re-renders when what its selector picks has changed (by `isEqual`, default identity).
 */
export function useLiveSelector<T>(
  selector: (snapshot: LiveSnapshot) => T,
  isEqual: (a: T, b: T) => boolean = Object.is,
): T {
  const client = useLiveClient();
  const cache = useRef<{ snapshot: LiveSnapshot; value: T } | null>(null);
  const getSnapshot = useCallback((): T => {
    const snapshot = client.getSnapshot();
    const cached = cache.current;
    if (cached && cached.snapshot === snapshot) return cached.value;
    const value = selector(snapshot);
    const kept = cached && isEqual(cached.value, value) ? cached.value : value;
    cache.current = { snapshot, value: kept };
    return kept;
  }, [client, selector, isEqual]);
  return useSyncExternalStore(client.subscribe, getSnapshot, getSnapshot);
}
