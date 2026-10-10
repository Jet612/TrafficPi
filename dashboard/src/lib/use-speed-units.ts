"use client";

import { api } from "./api";
import { useRequest } from "./hooks";
import type { SpeedUnits } from "./speed";

/**
 * The units speeds are written in (the owner's choice in Settings), or undefined until they are
 * known. A page that writes a speed asks once; until the answer comes it writes none.
 */
export function useSpeedUnits(): SpeedUnits | undefined {
  return useRequest("speed-units", (signal) => api.settings(signal)).data?.units;
}
