import { classSingular } from "./classes";
import { formatCount, fullTimeText } from "./format";
import { displayName, findElement } from "./profile";
import { SIZE_METRES, inUnits, roughSpeed, speedShort, unitName, type SpeedUnits } from "./speed";
import type { Profile, ReviewState, StopOut, StopOutcome, StopSummary, StopTotals } from "./types";

/** The outcomes, in the order they are told: the good one first, then from mild to grave, then the unknown. */
export const OUTCOMES: readonly StopOutcome[] = ["stopped", "rolling", "did_not_stop", "indeterminate"];

/** What each outcome is called on a tile and a badge. */
export const OUTCOME_LABEL: Record<StopOutcome, string> = {
  stopped: "Stopped",
  rolling: "Rolled through",
  did_not_stop: "Did not stop",
  indeterminate: "Unclear",
};

/** How an outcome reads in a sentence ("3 rolled through"). */
const OUTCOME_VERB: Record<StopOutcome, string> = {
  stopped: "stopped",
  rolling: "rolled through",
  did_not_stop: "did not stop",
  indeterminate: "unclear",
};

/** The status each outcome is shown with: always an icon and words as well as a colour. */
export const OUTCOME_LEVEL: Record<StopOutcome, "good" | "warning" | "critical" | "neutral"> = {
  stopped: "good",
  rolling: "warning",
  did_not_stop: "critical",
  indeterminate: "neutral",
};

/** The outcomes that are queued for a review: everything but a clean stop. */
export const NEEDS_REVIEW: readonly StopOutcome[] = ["rolling", "did_not_stop", "indeterminate"];

/** What a person made of a pass. A pass nobody has reviewed yet "needs review". */
export const REVIEW_LABEL: Record<ReviewState, string> = { confirmed: "Confirmed", false_positive: "False alarm" };

/** Whether a set of outcomes is the default view: the three that need review, in any order. */
export function isDefaultOutcomes(selected: readonly StopOutcome[]): boolean {
  return selected.length === NEEDS_REVIEW.length && NEEDS_REVIEW.every((name) => selected.includes(name));
}

/** Add or take away one outcome from those shown, keeping them in their usual order. */
export function toggleOutcome(selected: readonly StopOutcome[], outcome: StopOutcome): StopOutcome[] {
  const next = selected.includes(outcome) ? selected.filter((name) => name !== outcome) : [...selected, outcome];
  return OUTCOMES.filter((name) => next.includes(name));
}

/** What pressing a review button does: it sets that state, or, if it is the state already, takes it back. */
export function nextReview(current: ReviewState | null, pressed: ReviewState): ReviewState | null {
  return current === pressed ? null : pressed;
}

/**
 * Which passes to list by what a person made of them: "all" is every pass that still counts (the ones
 * not called a false alarm, which is what the tiles and the Data page add up), those nobody has reviewed
 * ("needs review"), or those called one thing.
 */
export type ReviewFilter = "all" | "unreviewed" | ReviewState;

/** What the list asks the server for: "all" is the passes that still count, so that the list agrees with the tiles. */
export function reviewQuery(filter: ReviewFilter): "counted" | "unreviewed" | ReviewState {
  return filter === "all" ? "counted" : filter;
}

export const REVIEW_FILTERS: readonly { value: ReviewFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "unreviewed", label: "Needs review" },
  { value: "confirmed", label: "Confirmed" },
  { value: "false_positive", label: "False alarms" },
];

/**
 * How many passes the list holds for these outcomes and this review filter, from the summary of the
 * period. "All" is the passes that still count, so its number is the tiles' (`counted`); the ones called
 * false alarms are listed under False alarms only. Null when a stop sign is picked: the summary is not
 * split by sign.
 */
export function listTotal(summary: StopSummary, selected: readonly StopOutcome[], review: ReviewFilter, signPicked: boolean): number | null {
  if (signPicked) return null;
  const pick = (totals: StopTotals) =>
    review === "all" ? counted(totals) : review === "unreviewed" ? totals.unreviewed : review === "confirmed" ? totals.confirmed : totals.false_positive;
  return summary.outcomes.filter((totals) => selected.includes(totals.outcome)).reduce((sum, totals) => sum + pick(totals), 0);
}

/** How a review is going for one outcome: how many are done, and how many are not. A clean stop is not in the queue. */
export function reviewProgress(totals: StopTotals): { reviewed: number; waiting: number; fraction: number } {
  const reviewed = totals.confirmed + totals.false_positive;
  return { reviewed, waiting: totals.unreviewed, fraction: totals.total === 0 ? 0 : reviewed / totals.total };
}

/** The outcomes that get no picture and no clip with the evidence settings as they are (a clean stop never gets any, and is not asked about). */
export function unkeptEvidence(
  settings: { evidence_enabled: boolean; evidence_keep_rolling: boolean; evidence_keep_did_not_stop: boolean },
  outcomes: readonly StopOutcome[],
): StopOutcome[] {
  return outcomes.filter((outcome) => {
    if (outcome === "stopped") return false;
    if (!settings.evidence_enabled) return true;
    return outcome === "rolling" ? !settings.evidence_keep_rolling : outcome === "did_not_stop" ? !settings.evidence_keep_did_not_stop : false;
  });
}

const OUTCOME_PASSES: Record<StopOutcome, string> = {
  stopped: "passes that stopped",
  rolling: "rolled-through passes",
  did_not_stop: "passes that did not stop",
  indeterminate: "unclear passes",
};

/** "rolled-through passes", "rolled-through passes and passes that did not stop". */
export function passesOf(outcomes: readonly StopOutcome[]): string {
  const names = outcomes.map((outcome) => OUTCOME_PASSES[outcome]);
  return names.length <= 1 ? (names[0] ?? "") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
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

/**
 * "12 vehicles reached a stop sign: 8 stopped, 3 rolled through, 1 did not stop." Every vehicle in the
 * count is one that reached the sign (a vehicle that could not be told is "unclear", still one of the
 * twelve); nothing is said of what did not happen.
 */
export function summarySentence(summary: StopSummary): string {
  const by = new Map(summary.outcomes.map((totals) => [totals.outcome, counted(totals)]));
  const all = OUTCOMES.reduce((sum, outcome) => sum + (by.get(outcome) ?? 0), 0);
  if (all === 0) return "No vehicle has reached a stop sign in this time.";
  const parts = OUTCOMES.flatMap((outcome) => ((by.get(outcome) ?? 0) > 0 ? [`${formatCount(by.get(outcome) ?? 0)} ${OUTCOME_VERB[outcome]}`] : []));
  return `${formatCount(all)} ${all === 1 ? "vehicle" : "vehicles"} reached a stop sign: ${parts.join(", ")}.`;
}

/** The slowest a vehicle went in the zone, as a rough speed in the units that are set: "slowest ≈ 4 mph", or "slowest almost still". */
export function slowestText(minSpeed: number, units: SpeedUnits): string {
  const speed = inUnits(minSpeed * SIZE_METRES, units);
  return speed < 1 ? "slowest almost still" : `slowest ≈ ${roughSpeed(speed)} ${unitName(units)}`;
}

/**
 * The speeds a calibrated camera measured for a pass, in the units that are set: "slowest 4 mph",
 * "at the line 7 mph" (and, in `detail`, "fastest 12 mph"). Only the speeds measured under the
 * calibration saved now (`calibrated`); an empty list says there are none, and the
 * rough estimate is told instead.
 */
export function measuredSpeedParts(
  stop: Pick<StopOut, "calibrated" | "min_speed_ms" | "max_speed_ms" | "line_speed_ms">,
  units: SpeedUnits,
  detail = false,
): string[] {
  if (!stop.calibrated) return [];
  const parts: string[] = [];
  if (stop.min_speed_ms !== null) parts.push(`slowest ${speedShort(stop.min_speed_ms, units, true)}`);
  if (stop.line_speed_ms !== null) parts.push(`at the line ${speedShort(stop.line_speed_ms, units, true)}`);
  if (detail && stop.max_speed_ms !== null) parts.push(`fastest ${speedShort(stop.max_speed_ms, units, true)}`);
  return parts;
}

/**
 * The numbers behind a verdict, for anyone who wants to see why: how long it was watched in the zone,
 * how long it stood still, and how fast it went. With a calibrated camera that is measured ("slowest 4
 * mph · at the line 7 mph"); without one it is how slowly it went at the least, a rough estimate (a
 * vehicle's own size stands for a car's), in the units set on the Setup page: "1.9 s in
 * zone · still 0.1 s · slowest ≈ 4 mph".
 */
export function numbersText(
  stop: Pick<StopOut, "hold_s" | "min_speed" | "observed_s" | "outcome" | "calibrated" | "min_speed_ms" | "max_speed_ms" | "line_speed_ms">,
  units: SpeedUnits,
  detail = false,
): string {
  const parts = [`${stop.observed_s.toFixed(1)} s in zone`];
  if (stop.outcome !== "indeterminate" || stop.hold_s > 0) parts.push(`still ${stop.hold_s.toFixed(1)} s`);
  const measured = measuredSpeedParts(stop, units, detail);
  if (measured.length > 0) parts.push(...measured);
  else if (stop.min_speed >= 0) parts.push(slowestText(stop.min_speed, units));
  return parts.join(" · ");
}

/** Which pass this is, in a few words, to say what a button or a picture belongs to: "car at Elm Street, Fri, Oct 9, 2026, 14:05:09". */
export function passText(stop: Pick<StopOut, "label" | "t">, sign: string, zone: string): string {
  return `${classSingular(stop.label ?? "").toLowerCase()} at ${sign}, ${fullTimeText(stop.t, zone)}`;
}

/** The name of a stop sign: its own, or its place in the list; a sign that was deleted since is said to be. */
export function signLabel(profile: Profile | null, id: string): string {
  if (profile === null) return "Stop sign";
  return findElement(profile, { kind: "stop", id }) ? displayName(profile, { kind: "stop", id }) : "Removed stop sign";
}

// --- passes just reviewed ------------------------------------------------------------------------

/**
 * The passes to list: what the server answered, with the changes made in this view, and the passes
 * reviewed in this view put back where they were if the server no longer lists them. A pass called a
 * false alarm leaves "All" (it no longer counts), and one confirmed leaves "Needs review"; if it
 * vanished the moment it was pressed, the Undo beside it and the window it was opened in would go with
 * it. Newest first, as the server sends them.
 *
 * A kept pass is put back only where the page reaches: it is not added beyond the oldest of a full page,
 * which a "Show more" would reach.
 */
export function listedPasses(
  fetched: readonly StopOut[],
  changed: ReadonlyMap<number, StopOut>,
  kept: ReadonlySet<number>,
  limit: number,
): StopOut[] {
  const rows = fetched.map((row) => changed.get(row.id) ?? row);
  const have = new Set(rows.map((row) => row.id));
  const oldest = fetched.length === 0 ? Number.NEGATIVE_INFINITY : (fetched[fetched.length - 1]?.id ?? Number.NEGATIVE_INFINITY);
  const reachable = (id: number) => fetched.length < limit || id > oldest;
  for (const id of kept) {
    const row = changed.get(id);
    if (row !== undefined && !have.has(id) && reachable(id)) rows.push(row);
  }
  return rows.sort((a, b) => b.id - a.id);
}
