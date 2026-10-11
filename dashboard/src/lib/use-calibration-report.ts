"use client";

import { useMemo } from "react";
import { api } from "./api";
import { probesOf } from "./calibration";
import { useRequest } from "./hooks";
import type { CalibrationReport, Profile } from "./types";

/** A quick run of changes (a corner being dragged) makes one request, this long after the last. */
export const REPORT_DELAY_MS = 350;

/**
 * What the server makes of the calibration being edited: how long it finds each known
 * distance, how many metres a pixel is at the stop lines and counting lines, and where speeds will
 * be given. `none` when the profile has no calibration; `problem` when it cannot be used, with the
 * reason; `failed` when the server could not be asked. The last report stays while the next one is
 * on its way, so that the shading and the numbers do not vanish while a corner is moved.
 */
export type ReportState =
  | { status: "none" }
  | { status: "loading"; report: CalibrationReport | null }
  | { status: "ok"; report: CalibrationReport }
  | { status: "problem"; problem: string }
  | { status: "failed"; report: CalibrationReport | null };

export function useCalibrationReport(profile: Profile): ReportState {
  const { calibration, lines, stop_signs: stopSigns } = profile;
  // Only the places the report is asked about, so that moving a zone asks nothing.
  const probes = useMemo(() => probesOf({ lines, stop_signs: stopSigns } as Profile), [lines, stopSigns]);
  const key = calibration === null ? null : JSON.stringify([calibration, probes]);
  const request = useRequest(
    key,
    (signal) => (calibration === null ? Promise.reject(new Error("no calibration")) : api.calibrationReport(calibration, probes, signal)),
    { delayMs: REPORT_DELAY_MS },
  );

  if (calibration === null) return { status: "none" };
  const answer = request.data;
  const answered = request.answeredKey === key;
  if (answered && answer !== null && !answer.ok) return { status: "problem", problem: answer.problem ?? "this calibration cannot be used" };
  const last = answer !== null && answer.ok ? answer.report : null;
  if (request.error !== null) return { status: "failed", report: last };
  if (answered && last !== null) return { status: "ok", report: last };
  return { status: "loading", report: last };
}
