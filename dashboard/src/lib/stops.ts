import { formatCount } from "./format";
import { displayName, findElement } from "./profile";
import type { Profile, ReviewState, StopOut, StopOutcome, StopSummary, StopTotals } from "./types";

/** The outcomes, in the order they are told: the good one first, then from mild to grave, then the unknown. */
export const OUTCOMES: readonly StopOutcome[] = ["stopped", "rolling", "did_not_stop", "indeterminate"];

/** What each outcome is called on a tile and a badge. */
export const OUTCOME_LABEL: Record<StopOutcome, string> = {
  stopped: "Stopped",
  rolling: "Rolled through",
  did_not_stop: "Did not stop",
  indeterminate: "Could not tell",
};

/** How an outcome reads in a sentence ("3 rolled through"). */
const OUTCOME_VERB: Record<StopOutcome, string> = {
  stopped: "stopped",
  rolling: "rolled through",
  did_not_stop: "did not stop",
  indeterminate: "could not be judged",
};

/** The status each outcome is shown with: always an icon and words as well as a colour. */
export const OUTCOME_LEVEL: Record<StopOutcome, "good" | "warning" | "critical" | "neutral"> = {
  stopped: "good",
  rolling: "warning",
  did_not_stop: "critical",
  indeterminate: "neutral",
};

/** The outcomes worth looking at: everything but a clean stop. */
export const NEEDS_A_LOOK: readonly StopOutcome[] = ["rolling", "did_not_stop", "indeterminate"];

export const REVIEW_LABEL: Record<ReviewState, string> = { confirmed: "Confirmed", false_positive: "False alarm" };

/** Add or take away one outcome from those shown, keeping them in their usual order. */
export function toggleOutcome(selected: readonly StopOutcome[], outcome: StopOutcome): StopOutcome[] {
  const next = selected.includes(outcome) ? selected.filter((name) => name !== outcome) : [...selected, outcome];
  return OUTCOMES.filter((name) => next.includes(name));
}

/** What pressing a review button does: it sets that state, or, if it is the state already, takes it back. */
export function nextReview(current: ReviewState | null, pressed: ReviewState): ReviewState | null {
  return current === pressed ? null : pressed;
}

// --- periods -------------------------------------------------------------------------------------

export type Period = "day" | "week" | "month";

const MINUTE_MS = 60_000;
const DAY_MS = 86_400_000;
export const PERIOD_OPTIONS: readonly { value: Period; label: string; ms: number }[] = [
  { value: "day", label: "24 hours", ms: DAY_MS },
  { value: "week", label: "7 days", ms: 7 * DAY_MS },
  { value: "month", label: "30 days", ms: 30 * DAY_MS },
];

/**
 * The window to look at: the last day, week or month up to the end of this minute. Whole minutes,
 * so that the same question is asked until the minute changes and a vehicle that has just crossed
 * the line is inside it.
 */
export function periodRange(period: Period, nowMs: number): { fromMs: number; toMs: number } {
  const length = PERIOD_OPTIONS.find((option) => option.value === period)?.ms ?? DAY_MS;
  const toMs = (Math.floor(nowMs / MINUTE_MS) + 1) * MINUTE_MS;
  return { fromMs: toMs - length, toMs };
}

// --- words ---------------------------------------------------------------------------------------

/** How many passes a total stands for: those that a person has not called false alarms. */
export const counted = (totals: StopTotals): number => totals.total - totals.false_positive;

/** "12 vehicles were judged: 8 stopped, 3 rolled through, 1 did not stop"; nothing is said of what did not happen. */
export function summarySentence(summary: StopSummary): string {
  const by = new Map(summary.outcomes.map((totals) => [totals.outcome, counted(totals)]));
  const all = OUTCOMES.reduce((sum, outcome) => sum + (by.get(outcome) ?? 0), 0);
  if (all === 0) return "No vehicle has been judged at a stop sign in this time.";
  const parts = OUTCOMES.flatMap((outcome) => ((by.get(outcome) ?? 0) > 0 ? [`${formatCount(by.get(outcome) ?? 0)} ${OUTCOME_VERB[outcome]}`] : []));
  return `${formatCount(all)} ${all === 1 ? "vehicle was" : "vehicles were"} judged: ${parts.join(", ")}.`;
}

/** The numbers behind a verdict, for anyone who wants to see why: how long it stood still, how slowly it went, how long it was watched. */
export function numbersText(stop: Pick<StopOut, "hold_s" | "min_speed" | "observed_s" | "outcome">): string {
  const parts = [`watched ${stop.observed_s.toFixed(1)} s`];
  if (stop.outcome !== "indeterminate" || stop.hold_s > 0) parts.unshift(`still for ${stop.hold_s.toFixed(1)} s`);
  if (stop.min_speed >= 0) parts.splice(parts.length - 1, 0, `slowest ${stop.min_speed.toFixed(1)} sizes a second`);
  return parts.join(" · ");
}

/** The name of a stop sign: its own, or its place in the list; a sign that was deleted since is said to be. */
export function signLabel(profile: Profile | null, id: string): string {
  if (profile === null) return "Stop sign";
  return findElement(profile, { kind: "stop", id }) ? displayName(profile, { kind: "stop", id }) : "Removed stop sign";
}
