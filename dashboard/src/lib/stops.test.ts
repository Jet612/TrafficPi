import { describe, expect, it } from "vitest";
import { stopsQuery } from "./api";
import { stopOf, profileOf } from "./fixtures";
import {
  NEEDS_REVIEW,
  OUTCOMES,
  OUTCOME_LABEL,
  OUTCOME_LEVEL,
  PERIOD_OPTIONS,
  REVIEW_FILTERS,
  counted,
  isDefaultOutcomes,
  listTotal,
  nextReview,
  numbersText,
  passText,
  passesOf,
  periodRange,
  reviewProgress,
  signLabel,
  slowestText,
  summarySentence,
  toggleOutcome,
  unkeptEvidence,
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
  it("are told in order and each has words, a status and a place among those that need review", () => {
    expect(OUTCOMES).toEqual(["stopped", "rolling", "did_not_stop", "indeterminate"]);
    expect(OUTCOMES.map((name) => OUTCOME_LABEL[name])).toEqual(["Stopped", "Rolled through", "Did not stop", "Unclear"]);
    expect(OUTCOMES.map((name) => OUTCOME_LEVEL[name])).toEqual(["good", "warning", "critical", "neutral"]);
    expect(NEEDS_REVIEW).toEqual(["rolling", "did_not_stop", "indeterminate"]);
  });

  it("are added and taken away in their usual order", () => {
    expect(toggleOutcome(["rolling"], "stopped")).toEqual(["stopped", "rolling"]);
    expect(toggleOutcome(["stopped", "rolling"], "rolling")).toEqual(["stopped"]);
    expect(toggleOutcome([], "indeterminate")).toEqual(["indeterminate"]);
    expect(toggleOutcome(["indeterminate", "rolling"], "did_not_stop")).toEqual(["rolling", "did_not_stop", "indeterminate"]);
  });
});

describe("the default view of the outcomes", () => {
  it("is the three that need review, in any order, and nothing else", () => {
    expect(isDefaultOutcomes(["rolling", "did_not_stop", "indeterminate"])).toBe(true);
    expect(isDefaultOutcomes(["indeterminate", "rolling", "did_not_stop"])).toBe(true);
    expect(isDefaultOutcomes(["rolling", "did_not_stop"])).toBe(false);
    expect(isDefaultOutcomes(["stopped", "rolling", "did_not_stop", "indeterminate"])).toBe(false);
    expect(isDefaultOutcomes([])).toBe(false);
  });
});

describe("the review statuses to filter by", () => {
  it("are all, those that need review, those confirmed and those called false alarms", () => {
    expect(REVIEW_FILTERS.map((option) => option.label)).toEqual(["All", "Needs review", "Confirmed", "False alarms"]);
    expect(REVIEW_FILTERS.map((option) => option.value)).toEqual(["all", "unreviewed", "confirmed", "false_positive"]);
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
      "12 vehicles reached a stop sign: 8 stopped, 3 rolled through, 1 did not stop.",
    );
    expect(summarySentence(summary(totals("rolling", 1)))).toBe("1 vehicle reached a stop sign: 1 rolled through.");
    expect(summarySentence(summary(totals("rolling", 4, { false_positive: 4, unreviewed: 0 })))).toBe(
      "No vehicle has reached a stop sign in this time.",
    );
    expect(summarySentence(summary())).toBe("No vehicle has reached a stop sign in this time.");
    // Those that could not be told are among the vehicles that reached the sign: the sentence no longer contradicts itself.
    expect(summarySentence(summary(totals("stopped", 170), totals("rolling", 568), totals("did_not_stop", 14), totals("indeterminate", 29)))).toBe(
      "781 vehicles reached a stop sign: 170 stopped, 568 rolled through, 14 did not stop, 29 unclear.",
    );
  });
});

describe("which pass it is", () => {
  it("names the vehicle, the stop sign and the second, in the zone of the camera", () => {
    const at = Date.UTC(2026, 9, 9, 14, 5, 9);
    expect(passText({ label: "car", t: at }, "Elm Street", "UTC")).toBe("car at Elm Street, Fri, Oct 9, 2026, 14:05:09");
    expect(passText({ label: "truck", t: at }, "Stop sign 2", "America/New_York")).toBe("truck at Stop sign 2, Fri, Oct 9, 2026, 10:05:09");
    expect(passText({ label: null, t: at }, "Elm Street", "UTC")).toBe("vehicle at Elm Street, Fri, Oct 9, 2026, 14:05:09");
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
    min_speed_ms: null,
    max_speed_ms: null,
    line_speed_ms: null,
    review: null,
    reviewed_at: null,
    snapshot_url: null,
    clip_url: null,
    ...over,
  });

  it("say how long it was in the zone, how long it stood still and how slowly it went, in the units that are set", () => {
    // 0.31 sizes a second is 0.31 x 2.5 m a second: about 1.7 mph, which is told as 2.
    expect(numbersText(stop(), "mph")).toBe("4.2 s in zone · still 0.4 s · slowest ≈ 2 mph");
    expect(numbersText(stop(), "kmh")).toBe("4.2 s in zone · still 0.4 s · slowest ≈ 3 km/h");
  });

  it("leave out a speed that was not measured, and a wait for one that could not be judged", () => {
    expect(numbersText(stop({ min_speed: -1 }), "mph")).toBe("4.2 s in zone · still 0.4 s");
    expect(numbersText(stop({ outcome: "indeterminate", hold_s: 0, min_speed: -1, observed_s: 0.4 }), "mph")).toBe("0.4 s in zone");
    expect(numbersText(stop({ outcome: "indeterminate", hold_s: 0.3, min_speed: 1.234, observed_s: 0.7 }), "mph")).toBe(
      "0.7 s in zone · still 0.3 s · slowest ≈ 7 mph",
    );
  });

  it("call a vehicle that all but stopped almost still, not 0 mph", () => {
    expect(slowestText(0, "mph")).toBe("slowest almost still");
    expect(slowestText(0.1, "kmh")).toBe("slowest almost still"); // 0.9 km/h
    expect(slowestText(1, "mph")).toBe("slowest ≈ 6 mph"); // 2.5 m/s is 5.6 mph
    expect(slowestText(2, "mph")).toBe("slowest ≈ 10 mph"); // 11.2 mph, told to the nearest 5 from 10 up
  });
});

describe("how many passes a list holds", () => {
  const all = summary(
    totals("stopped", 17),
    totals("rolling", 38, { false_positive: 3, confirmed: 2, unreviewed: 33 }),
    totals("did_not_stop", 4, { unreviewed: 4 }),
    totals("indeterminate", 25, { confirmed: 1, unreviewed: 24 }),
  );

  it("adds up the outcomes picked, by the review filter", () => {
    expect(listTotal(all, NEEDS_REVIEW, "all", false)).toBe(38 + 4 + 25);
    expect(listTotal(all, NEEDS_REVIEW, "unreviewed", false)).toBe(33 + 4 + 24);
    expect(listTotal(all, NEEDS_REVIEW, "confirmed", false)).toBe(3);
    expect(listTotal(all, NEEDS_REVIEW, "false_positive", false)).toBe(3);
    expect(listTotal(all, ["stopped"], "all", false)).toBe(17);
    expect(listTotal(all, [], "all", false)).toBe(0);
  });

  it("cannot be told for one stop sign, which the summary is not split by", () => {
    expect(listTotal(all, NEEDS_REVIEW, "all", true)).toBeNull();
  });

  it("counts the passes called false alarms, which the tiles leave out", () => {
    expect(counted(totals("rolling", 38, { false_positive: 3 }))).toBe(35);
    expect(listTotal(all, ["rolling"], "all", false)).toBe(38);
  });
});

describe("how a review is going", () => {
  it("says how many are done, how many wait and what share of the whole is done", () => {
    expect(reviewProgress(totals("rolling", 40, { confirmed: 6, false_positive: 4, unreviewed: 30 }))).toEqual({ reviewed: 10, waiting: 30, fraction: 0.25 });
    expect(reviewProgress(totals("rolling", 0))).toEqual({ reviewed: 0, waiting: 0, fraction: 0 });
  });
});

describe("evidence that is not kept", () => {
  const on = { evidence_enabled: true, evidence_keep_rolling: true, evidence_keep_did_not_stop: true };

  it("is nothing when it is all on, and never a clean stop's", () => {
    expect(unkeptEvidence(on, OUTCOMES)).toEqual([]);
    expect(unkeptEvidence({ ...on, evidence_enabled: false }, OUTCOMES)).toEqual(["rolling", "did_not_stop", "indeterminate"]);
  });

  it("follows each outcome's own switch, and the main one for a pass that could not be told", () => {
    expect(unkeptEvidence({ ...on, evidence_keep_rolling: false }, NEEDS_REVIEW)).toEqual(["rolling"]);
    expect(unkeptEvidence({ ...on, evidence_keep_did_not_stop: false }, NEEDS_REVIEW)).toEqual(["did_not_stop"]);
    expect(unkeptEvidence({ ...on, evidence_keep_rolling: false }, ["did_not_stop"])).toEqual([]);
  });

  it("is named in words", () => {
    expect(passesOf([])).toBe("");
    expect(passesOf(["rolling"])).toBe("rolled-through passes");
    expect(passesOf(["rolling", "did_not_stop"])).toBe("rolled-through passes and passes that did not stop");
    expect(passesOf(["rolling", "did_not_stop", "indeterminate"])).toBe("rolled-through passes, passes that did not stop and unclear passes");
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
