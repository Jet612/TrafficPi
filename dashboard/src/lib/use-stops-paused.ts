"use client";

import { api } from "./api";
import { usePoll } from "./hooks";

const REFRESH_MS = 60_000;

/**
 * Whether stop signs are not being judged now: they have hours (daylight and/or set times) and this is
 * not one of them. False while it is not known, and for a setup with no hours. The live
 * view draws a sign that is not judging faint, and says so.
 */
export function useStopsPaused(): boolean {
  const hours = usePoll((signal) => api.stopHours(signal), REFRESH_MS).data;
  return hours !== null && hours.status.limited && !hours.status.judging;
}
