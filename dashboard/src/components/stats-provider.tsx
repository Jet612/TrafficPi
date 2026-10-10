"use client";

import { createContext, useContext, useEffect, useMemo, useRef } from "react";
import { useLiveSelector } from "@/components/live-provider";
import { api } from "@/lib/api";
import { usePoll } from "@/lib/hooks";
import type { EventOut, TodaySummary } from "@/lib/types";

interface Stats {
  today: TodaySummary | null;
  events: EventOut[] | null;
  /** True once today's summary has been fetched, with an answer or an error. */
  loaded: boolean;
  failed: boolean;
}

const StatsContext = createContext<Stats | null>(null);

export function useStats(): Stats {
  const stats = useContext(StatsContext);
  if (!stats) throw new Error("useStats needs a StatsProvider above it");
  return stats;
}

const POLL_MS = 10_000;

/** Today's numbers, fetched once for every card that shows them, and again when a vehicle is counted. */
export function StatsProvider({ children }: { children: React.ReactNode }) {
  const today = usePoll((signal) => api.today(signal), POLL_MS);
  const events = usePoll((signal) => api.events(10, signal), POLL_MS);

  // A new count arrives on the live socket well before the next poll: fetch right away, but once
  // for a burst of them.
  const newest = useLiveSelector((snapshot) => {
    const latest = snapshot.state?.events[0];
    return latest ? `${latest.t}:${latest.track}:${latest.line}` : "";
  });
  const refreshers = useRef({ today: today.refresh, events: events.refresh });
  useEffect(() => {
    refreshers.current = { today: today.refresh, events: events.refresh };
  });
  useEffect(() => {
    if (!newest) return;
    const timer = setTimeout(() => {
      refreshers.current.today();
      refreshers.current.events();
    }, 400);
    return () => clearTimeout(timer);
  }, [newest]);

  const value = useMemo<Stats>(
    () => ({
      today: today.data,
      events: events.data,
      loaded: today.loaded,
      failed: today.error !== null && today.data === null,
    }),
    [today.data, today.loaded, today.error, events.data],
  );
  return <StatsContext.Provider value={value}>{children}</StatsContext.Provider>;
}
