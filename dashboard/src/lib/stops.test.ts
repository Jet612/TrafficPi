import { describe, expect, it } from "vitest";
import { stopsQuery } from "./api";
import { stopOf, profileOf } from "./fixtures";
import {
  NEEDS_A_LOOK,
  OUTCOMES,
  OUTCOME_LABEL,
  OUTCOME_LEVEL,
  PERIOD_OPTIONS,
  counted,
  nextReview,
  numbersText,
  periodRange,
  signLabel,
  summarySentence,
  toggleOutcome,
} from "./stops";
import type { StopOut, StopSummary, StopTotals } from "./types";

const totals = (outcome: StopTotals["outcome"], total: number, over: Partial<StopTotals> = {}): StopTotals => ({
  outcome,
  total,
  confirmed: 0,
  false_positive: 0,
  unreviewed: total,
  ...over,
});
const summary = (...parts: StopTotals[]): StopSummary => ({
  from_ms: 0,
  to_ms: 1,
  outcomes: OUTCOMES.map((name) => parts.find((part) => part.outcome === name) ?? totals(name, 0)),
});

describe("the four outcomes", () => {
  it("are told in order and each has words, a status and a place among those that need a look", () => {
    expect(OUTCOMES).toEqual(["stopped", "rolling", "did_not_stop", "indeterminate"]);
    expect(OUTCOMES.map((name) => OUTCOME_LABEL[name])).toEqual(["Stopped", "Rolled through", "Did not stop", "Could not tell"]);
    expect(OUTCOMES.map((name) => OUTCOME_LEVEL[name])).toEqual(["good", "warning", "critical", "neutral"]);
    expect(NEEDS_A_LOOK).toEqual(["rolling", "did_not_stop", "indeterminate"]);
  });

  it("are added and taken away in their usual order", () => {
    expect(toggleOutcome(["rolling"], "stopped")).toEqual(["stopped", "rolling"]);
    expect(toggleOutcome(["stopped", "rolling"], "rolling")).toEqual(["stopped"]);
    expect(toggleOutcome([], "indeterminate")).toEqual(["indeterminate"]);
    expect(toggleOutcome(["indeterminate", "rolling"], "did_not_stop")).toEqual(["rolling", "did_not_stop", "indeterminate"]);
  });
});

describe("a review", () => {
  it("is set by pressing its button, and taken back by pressing it again", () => {
    expect(nextReview(null, "confirmed")).toBe("confirmed");
    expect(nextReview("confirmed", "confirmed")).toBeNull();
    expect(nextReview("confirmed", "false_positive")).toBe("false_positive");
    expect(nextReview("false_positive", "false_positive")).toBeNull();
  });
});

describe("the period looked at", () => {
  const now = Date.UTC(2026, 9, 9, 14, 5, 9, 500);

  it("is the last day, week or month, up to the end of this minute, so that what has just happened is in it", () => {
    const minuteEnd = Date.UTC(2026, 9, 9, 14, 6);
    expect(periodRange("day", now)).toEqual({ fromMs: minuteEnd - 86_400_000, toMs: minuteEnd });
    expect(periodRange("week", now).toMs - periodRange("week", now).fromMs).toBe(7 * 86_400_000);
    expect(periodRange("month", now).toMs - periodRange("month", now).fromMs).toBe(30 * 86_400_000);
    expect(periodRange("day", now).toMs).toBeGreaterThan(now);
  });

  it("is the same question until the minute changes", () => {
    expect(periodRange("day", now)).toEqual(periodRange("day", now + 30_000));
    expect(periodRange("day", now)).not.toEqual(periodRange("day", now + 60_000));
  });

  it("has a name for each option", () => {
    expect(PERIOD_OPTIONS.map((option) => option.label)).toEqual(["24 hours", "7 days", "30 days"]);
  });
});

describe("the summary in words", () => {
  it("counts what a person has not called a false alarm, and says only what happened", () => {
    expect(counted(totals("rolling", 5, { false_positive: 2, unreviewed: 3 }))).toBe(3);
    expect(summarySentence(summary(totals("stopped", 8), totals("rolling", 3), totals("did_not_stop", 1)))).toBe(
      "12 vehicles were judged: 8 stopped, 3 rolled through, 1 did not stop.",
    );
    expect(summarySentence(summary(totals("rolling", 1)))).toBe("1 vehicle was judged: 1 rolled through.");
    expect(summarySentence(summary(totals("rolling", 4, { false_positive: 4, unreviewed: 0 })))).toBe(
      "No vehicle has been judged at a stop sign in this time.",
    );
    expect(summarySentence(summary())).toBe("No vehicle has been judged at a stop sign in this time.");
    expect(summarySentence(summary(totals("indeterminate", 1200)))).toBe("1,200 vehicles were judged: 1,200 could not be judged.");
  });
});

describe("the numbers behind a verdict", () => {
  const stop = (over: Partial<StopOut> = {}): StopOut => ({
    id: 1,
    t: 0,
    sign: "elm",
    label: "car",
    outcome: "rolling",
    hold_s: 0.4,
    min_speed: 0.31,
    observed_s: 4.2,
    review: null,
    reviewed_at: null,
    ...over,
  });

  it("say how long it stood still, how slowly it went and how long it was watched", () => {
    expect(numbersText(stop())).toBe("still for 0.4 s · slowest 0.3 sizes a second · watched 4.2 s");
  });

  it("leave out a speed that was not measured, and a wait for one that could not be judged", () => {
    expect(numbersText(stop({ min_speed: -1 }))).toBe("still for 0.4 s · watched 4.2 s");
    expect(numbersText(stop({ outcome: "indeterminate", hold_s: 0, min_speed: -1, observed_s: 0.4 }))).toBe("watched 0.4 s");
    expect(numbersText(stop({ outcome: "indeterminate", hold_s: 0.3, min_speed: 1.234, observed_s: 0.7 }))).toBe(
      "still for 0.3 s · slowest 1.2 sizes a second · watched 0.7 s",
    );
  });
});

describe("a stop sign's name", () => {
  it("is its own, or its place in the list, or says that it was removed", () => {
    const profile = profileOf({ stops: [stopOf("a", { name: "Elm Street" }), stopOf("b")] });
    expect(signLabel(profile, "a")).toBe("Elm Street");
    expect(signLabel(profile, "b")).toBe("Stop sign 2");
    expect(signLabel(profile, "gone")).toBe("Removed stop sign");
    expect(signLabel(null, "a")).toBe("Stop sign");
  });
});

describe("asking the server", () => {
  it("puts only what was asked for in the query", () => {
    expect(stopsQuery({ fromMs: 10, toMs: 20 })).toBe("from_ms=10&to_ms=20");
    expect(stopsQuery({ fromMs: 10, toMs: 20, outcomes: ["rolling", "did_not_stop"], review: "unreviewed", sign: "stop-1", limit: 25, beforeId: 99 })).toBe(
      "from_ms=10&to_ms=20&outcome=rolling%2Cdid_not_stop&review=unreviewed&sign=stop-1&limit=25&before_id=99",
    );
  });
});
