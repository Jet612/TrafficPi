/**
 * The files `scripts/record_demo.py` writes from a running made-up street, which the static demo
 * answers from. They are served beside the dashboard, under `/demo/`.
 */
import type { LiveState, StopOut, TrailPaths } from "@/lib/types";

export const RECORDING_FORMAT = 1;

export interface TrailDayFile {
  /** The local day, "2026-11-09". */
  day: string;
  from_ms: number;
  to_ms: number;
  /** Where its paths are, relative to the site: "/demo/trails/2026-11-09.json". */
  url: string;
}

/** `/demo/recording.json`. */
export interface Recording {
  format: number;
  /** When the live loop began (ms since the epoch). */
  recorded_at: number;
  /** The answers to plain GET requests, by `requestKey`. */
  get: Record<string, unknown>;
  /** Every pass at a stop sign, newest first. */
  stops: StopOut[];
  trail_days: TrailDayFile[];
}

/** What changes from one live message to the next; the rest is in `LiveRecording.base`. */
export type LiveFrame = Pick<LiveState, "frame" | "uptime_s" | "fps" | "tracks" | "events"> & {
  /** Seconds from the start of the loop, on the video's clock. */
  t: number;
};

/** `/demo/live.json`: the live socket's messages over one loop of `/demo/street.mp4`. */
export interface LiveRecording {
  duration_s: number;
  base: Omit<LiveState, keyof LiveFrame | "type" | "updated_ms">;
  frames: LiveFrame[];
}

/** A trail day's file holds the answer to `/api/trails` over the whole day. */
export type TrailDayPaths = TrailPaths;

/**
 * The key a GET answer is kept under: the path, then the query with its parameters sorted. The
 * recorder builds the same key (`request_key` in scripts/record_demo.py).
 */
export function requestKey(pathname: string, search: URLSearchParams | string): string {
  const params = [...new URLSearchParams(search)].sort(([a, x], [b, y]) => (a === b ? (x < y ? -1 : x > y ? 1 : 0) : a < b ? -1 : 1));
  const query = new URLSearchParams(params).toString();
  return query === "" ? pathname : `${pathname}?${query}`;
}
