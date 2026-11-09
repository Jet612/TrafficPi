/**
 * The demo's clocks. The page's clock is moved back to when the recording was made, so that "today"
 * and "the last 24 hours" are the recording's; it runs on from there. The live view loops one
 * recorded stretch of the street: the live messages follow the video, so the boxes stay on the
 * vehicles.
 */
import type { LiveState } from "@/lib/types";
import type { LiveFrame, LiveRecording } from "./recording";
import type { LiveOverlay } from "./server";

/** Make `Date.now()` and `new Date()` start at `startMs` and run on from there. */
export function shiftClock(startMs: number): void {
  const RealDate = Date;
  const offset = startMs - RealDate.now();
  class DemoDate extends RealDate {
    constructor(...args: unknown[]) {
      if (args.length === 0) super(RealDate.now() + offset);
      else super(...(args as [number]));
    }
    static now(): number {
      return RealDate.now() + offset;
    }
  }
  globalThis.Date = DemoDate as DateConstructor;
}

/**
 * Kept on the window, not in this module: the code that runs before the dashboard starts and the
 * dashboard itself are separate bundles, and each may have its own copy of this module.
 */
interface Shared {
  anchorMs?: number;
  video?: HTMLVideoElement | null;
}
function shared(): Shared {
  const holder = globalThis as { __trafficpiDemo?: Shared };
  holder.__trafficpiDemo ??= {};
  return holder.__trafficpiDemo;
}

/** Start the loop now (once). */
export function startPlayback(): void {
  shared().anchorMs ??= Date.now();
}

/** The live view's video, while it is showing: the loop then keeps to it. */
export function registerVideo(video: HTMLVideoElement | null): void {
  shared().video = video;
}

/**
 * How many whole loops have played, and where in the current one we are (seconds). While the video
 * is showing, where it is decides, so that the boxes stay on the vehicles even when the video
 * stalls or starts late; otherwise the time since the page opened does.
 */
export function playbackPosition(durationS: number, nowMs: number = Date.now()): { loop: number; t: number } {
  startPlayback();
  const { anchorMs = nowMs, video } = shared();
  if (!(durationS > 0)) return { loop: 0, t: 0 };
  const elapsed = Math.max(0, (nowMs - anchorMs) / 1000);
  const loop = Math.floor(elapsed / durationS);
  if (video && video.readyState >= 2 && Math.abs(video.duration - durationS) < 0.5) {
    return { loop, t: Math.min(video.currentTime, durationS) };
  }
  return { loop, t: elapsed - loop * durationS };
}

/** The index of the last frame at or before `t` (frames are in order of `t`); the first if none is. */
export function frameAt(frames: readonly LiveFrame[], t: number): number {
  let lo = 0;
  let hi = frames.length - 1;
  while (lo < hi) {
    const mid = (lo + hi + 1) >> 1;
    if ((frames[mid]?.t ?? Infinity) <= t) lo = mid;
    else hi = mid - 1;
  }
  return lo;
}

/**
 * The live message for one frame of the loop. Times in it are moved on by the loops already
 * played, so that a crossing replayed on the second loop is a new one, not one from a minute ago.
 */
export function liveMessage(
  recording: LiveRecording,
  index: number,
  loop: number,
  nowMs: number,
  overlay: LiveOverlay | null,
): LiveState {
  const { frame, uptime_s, fps, tracks, events } = recording.frames[index] ?? recording.frames[0]!;
  const shiftMs = Math.round(loop * recording.duration_s * 1000);
  return {
    ...recording.base,
    ...(overlay ?? {}),
    type: "state",
    frame,
    fps,
    tracks,
    uptime_s: uptime_s + loop * recording.duration_s,
    updated_ms: nowMs,
    events: events.map((event) => ({ ...event, t: event.t + shiftMs })),
  } as LiveState;
}
