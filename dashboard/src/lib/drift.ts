import { formatAgo } from "./format";

/**
 * What the camera-drift check has found: has the view moved since the reference picture was kept?
 * It mirrors `DriftMonitor.status()` in backend/trafficpi/vision/drift.py (plan.md D40), which the
 * vision service sends in every live state message.
 */
export type DriftState = "ok" | "drifted" | "unknown" | "no_reference";

export interface DriftStatus {
  state: DriftState;
  /** One sentence for the owner; for `drifted` it says what moved and by how much. */
  message: string;
  /** How far the drawn lines and zones are off, in % of the picture's width (the last sure look). */
  shift_pct: number | null;
  /** The middle of the picture moved right (+) or left (-), in % of its width. */
  dx_pct: number | null;
  /** ... and down (+) or up (-), in % of its width. */
  dy_pct: number | null;
  /** Turned clockwise (+) or anticlockwise (-), in degrees. */
  rotation_deg: number | null;
  /** The scenery looks bigger (>1) or smaller (<1) than it did. */
  scale: number | null;
  /** How well the two pictures line up once the move is undone, 0 to 1. */
  alignment: number | null;
  /** The last look, whatever it found (ms since the epoch). */
  checked_at: number | null;
  /** The last look that could tell (ms). */
  sure_at: number | null;
  /** Why the latest look could not tell (night, fog, something in front of the lens). */
  unsure_because: string | null;
}

const STATES: readonly string[] = ["ok", "drifted", "unknown", "no_reference"];

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const numberOrNull = (value: unknown): number | null =>
  typeof value === "number" && Number.isFinite(value) ? value : null;

const textOrNull = (value: unknown): string | null => (typeof value === "string" && value !== "" ? value : null);

/**
 * Read the `drift` block of a live state message. Null when there is none (a service that keeps no
 * check sends an empty block, and an older one sends nothing) or when it is not one.
 */
export function parseDrift(value: unknown): DriftStatus | null {
  if (!isRecord(value) || typeof value.state !== "string" || !STATES.includes(value.state)) return null;
  return {
    state: value.state as DriftState,
    message: typeof value.message === "string" ? value.message : "",
    shift_pct: numberOrNull(value.shift_pct),
    dx_pct: numberOrNull(value.dx_pct),
    dy_pct: numberOrNull(value.dy_pct),
    rotation_deg: numberOrNull(value.rotation_deg),
    scale: numberOrNull(value.scale),
    alignment: numberOrNull(value.alignment),
    checked_at: numberOrNull(value.checked_at),
    sure_at: numberOrNull(value.sure_at),
    unsure_because: textOrNull(value.unsure_because),
  };
}

/** Whether two readings say the same, so that a live selector does not re-render ten times a second. */
export function sameDrift(a: DriftStatus | null, b: DriftStatus | null): boolean {
  if (a === null || b === null) return a === b;
  return (Object.keys(a) as (keyof DriftStatus)[]).every((key) => a[key] === b[key]);
}

export type DriftLevel = "good" | "warning" | "neutral";

/** Only a view that has moved is a warning; not being sure is not one. */
export function driftLevel(drift: DriftStatus | null): DriftLevel {
  if (drift?.state === "ok") return "good";
  if (drift?.state === "drifted") return "warning";
  return "neutral";
}

export function driftLabel(drift: DriftStatus | null): string {
  switch (drift?.state) {
    case "ok":
      return "In place";
    case "drifted":
      return "Has moved";
    case "unknown":
      return "Not sure yet";
    case "no_reference":
      return "No reference picture";
    default:
      return "Not checked";
  }
}

const trimmed = (value: number, digits: number): string => value.toFixed(digits).replace(/^-(?=0(\.0+)?$)/, "");

/** How far the picture has moved, as short phrases: "right 0.3%, down 0.2%". Empty when it has not. */
export function describeMove(drift: DriftStatus): string {
  const parts: string[] = [];
  const { dx_pct: dx, dy_pct: dy } = drift;
  if (dx !== null && Math.abs(dx) >= 0.05) parts.push(`${dx > 0 ? "right" : "left"} ${trimmed(Math.abs(dx), 1)}%`);
  if (dy !== null && Math.abs(dy) >= 0.05) parts.push(`${dy > 0 ? "down" : "up"} ${trimmed(Math.abs(dy), 1)}%`);
  return parts.join(", ");
}

/** "0.3 degrees clockwise"; empty when it has not turned. */
export function describeTurn(drift: DriftStatus): string {
  const degrees = drift.rotation_deg;
  if (degrees === null || Math.abs(degrees) < 0.05) return "";
  return `${trimmed(Math.abs(degrees), 1)} degrees ${degrees > 0 ? "clockwise" : "anticlockwise"}`;
}

/** "1% bigger"; empty when its size has not changed. */
export function describeSize(drift: DriftStatus): string {
  const scale = drift.scale;
  if (scale === null || Math.abs(scale - 1) < 0.002) return "";
  return `${trimmed(Math.abs(scale - 1) * 100, 1)}% ${scale > 1 ? "bigger" : "smaller"}`;
}

/** When the check last looked, and when it last could tell, for the card's small print. */
export function describeChecks(drift: DriftStatus | null, nowMs: number): string {
  if (drift === null) return "The vision service is not checking the camera's view.";
  if (drift.checked_at === null) return "Not checked yet.";
  const looked = `Last looked ${formatAgo(drift.checked_at, nowMs)}`;
  if (drift.sure_at === null) return `${looked}; it could not tell.`;
  if (drift.sure_at === drift.checked_at) return `${looked}.`;
  return `${looked}; the last look that could tell was ${formatAgo(drift.sure_at, nowMs)}.`;
}
