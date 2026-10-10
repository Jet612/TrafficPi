export type Severity = "ok" | "warning" | "critical";

/** The Pi throttles (slows itself down) from 80 degrees and hard from 85. */
export function temperatureSeverity(celsius: number): Severity {
  if (celsius >= 85) return "critical";
  if (celsius >= 80) return "warning";
  return "ok";
}

export function diskSeverity(usedPercent: number): Severity {
  if (usedPercent >= 95) return "critical";
  if (usedPercent >= 85) return "warning";
  return "ok";
}

export function cpuSeverity(percent: number): Severity {
  if (percent >= 95) return "critical";
  if (percent >= 85) return "warning";
  return "ok";
}

// The flag texts come from the vision service (backend/trafficpi/vision/metrics.py).
const THROTTLE_MEANINGS: Record<string, string> = {
  "under-voltage": "the power supply is too weak",
  "ARM frequency capped": "the CPU speed is capped",
  throttled: "the CPU is throttled",
  "soft temperature limit": "it is running hot",
  "under-voltage occurred": "the power supply dipped",
  "ARM frequency cap occurred": "the CPU speed was capped",
  "throttling occurred": "the CPU was throttled",
  "soft temperature limit occurred": "it ran hot",
};

/** What a throttle flag from the Pi means, in words. Unknown flags are shown as they are. */
export function describeThrottle(flag: string): string {
  return THROTTLE_MEANINGS[flag] ?? flag;
}

/** The word that goes with a processor temperature, so that the number is not left to be read against the limits. */
export function temperatureWord(celsius: number): string {
  if (celsius >= 85) return "Throttling hard";
  if (celsius >= 80) return "Throttling";
  if (celsius >= 70) return "Warm";
  return "Normal";
}
