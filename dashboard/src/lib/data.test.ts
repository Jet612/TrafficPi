import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  DATA_PERIODS,
  MIN_SAMPLE,
  OUTCOME_COLOR,
  OUTCOME_SERIES,
  approximateSpeed,
  asShares,
  averagePerFinishedDay,
  changeInPoints,
  changeTone,
  classSeries,
  comparedWith,
  dayLabel,
  dayTitle,
  outcomeDayRows,
  outcomeHourRows,
  peak,
  periodDays,
  pointsText,
  rangeText,
  rateExtremes,
  sharesOf,
  speedText,
  stopRate,
  stopRateText,
  toldOf,
  totalOf,
  trafficDayRows,
  trafficHourRows,
  violationsOf,
  weekdayName,
  type OutcomeCounts,
} from "./data";

const counts = (stopped = 0, rolling = 0, did_not_stop = 0, indeterminate = 0): OutcomeCounts => ({ stopped, rolling, did_not_stop, indeterminate });

describe("the periods", () => {
  it("are asked of the server as a number of local days, 0 for all time", () => {
    expect(DATA_PERIODS.map((option) => [option.value, option.days])).toEqual([
      ["today", 1],
      ["week", 7],
      ["month", 30],
      ["quarter", 90],
      ["all", 0],
    ]);
    expect(periodDays("month")).toBe(30);
    expect(periodDays("all")).toBe(0);
    expect(periodDays("nonsense" as never)).toBe(7);
  });

  it("say what they are compared with, and all time is compared with nothing", () => {
    expect(comparedWith("today")).toBe("yesterday");
    expect(comparedWith("week")).toBe("the 7 days before");
    expect(comparedWith("quarter")).toBe("the 90 days before");
    expect(comparedWith("all")).toBeNull();
  });
});

describe("counting vehicles", () => {
  it("adds up what was judged, what could be told and what went wrong", () => {
    const c = counts(10, 4, 2, 3);
    expect(totalOf(c)).toBe(19);
    expect(toldOf(c)).toBe(16);
    expect(violationsOf(c)).toBe(6);
  });
});

describe("shares", () => {
  it("are whole percentages that add up to 100, whatever the split", () => {
    expect(sharesOf(counts(1, 1, 1, 0))).toEqual({ stopped: 34, rolling: 33, did_not_stop: 33, indeterminate: 0 });
    expect(sharesOf(counts(2, 1))).toEqual({ stopped: 67, rolling: 33, did_not_stop: 0, indeterminate: 0 });
    // 64.2, 20.3, 10.1 and 5.4: the point left over goes to the largest remainder, the 0.4
    expect(sharesOf(counts(95, 30, 15, 8))).toEqual({ stopped: 64, rolling: 20, did_not_stop: 10, indeterminate: 6 });
    expect(sharesOf(counts(0, 0, 0, 7))).toEqual({ stopped: 0, rolling: 0, did_not_stop: 0, indeterminate: 100 });
  });

  it("are all 0 when nothing was judged", () => {
    expect(sharesOf(counts())).toEqual({ stopped: 0, rolling: 0, did_not_stop: 0, indeterminate: 0 });
  });

  it("always add up to 100, are within a point of the truth, and never give an outcome that did not happen a share", () => {
    let seed = 12345;
    const next = (limit: number) => {
      seed = (seed * 1103515245 + 12345) % 2 ** 31;
      return seed % limit;
    };
    for (let round = 0; round < 500; round++) {
      const c = counts(next(40), next(40), next(40), next(40));
      const total = totalOf(c);
      if (total === 0) continue;
      const shares = sharesOf(c);
      expect(shares.stopped + shares.rolling + shares.did_not_stop + shares.indeterminate).toBe(100);
      for (const key of ["stopped", "rolling", "did_not_stop", "indeterminate"] as const) {
        expect(Math.abs(shares[key] - (c[key] / total) * 100)).toBeLessThan(1);
        if (c[key] === 0) expect(shares[key]).toBe(0);
      }
    }
  });

  it("do not lose a point to a share that floating point puts just under a whole number", () => {
    // 29/100 is 28.999999999999996 in floating point
    expect(sharesOf(counts(29, 71))).toMatchObject({ stopped: 29, rolling: 71 });
    expect(sharesOf(counts(58, 42))).toMatchObject({ stopped: 58, rolling: 42 });
  });
});

describe("shares across the two languages", () => {
  it("are the ones the daily email says: every case in the shared file comes out the same here and in Python", () => {
    const file = JSON.parse(readFileSync(new URL("../../../backend/tests/data/share_cases.json", import.meta.url), "utf8")) as {
      cases: { counts: OutcomeCounts; shares: OutcomeCounts }[];
    };
    expect(file.cases.length).toBeGreaterThan(600);
    const wrong = file.cases.filter((c) => JSON.stringify(sharesOf(c.counts)) !== JSON.stringify(c.shares));
    expect(wrong.slice(0, 3)).toEqual([]);
  });
});

describe("the change since the period before", () => {
  it("is the difference of the shares as they are shown", () => {
    expect(changeInPoints(counts(64, 20, 10, 6), counts(60, 25, 10, 5), "stopped")).toBe(4);
    expect(changeInPoints(counts(64, 20, 10, 6), counts(60, 25, 10, 5), "rolling")).toBe(-5);
    expect(changeInPoints(counts(5, 5), counts(5, 5), "stopped")).toBe(0);
  });

  it("is not there when either period has no vehicles, or there is no period before", () => {
    expect(changeInPoints(counts(5), null, "stopped")).toBeNull();
    expect(changeInPoints(counts(5), counts(), "stopped")).toBeNull();
    expect(changeInPoints(counts(), counts(5), "stopped")).toBeNull();
  });

  it("is welcome when more stop, or fewer roll or miss, and neither when it is about what could not be told", () => {
    expect(changeTone("stopped", 3)).toBe("good");
    expect(changeTone("stopped", -3)).toBe("bad");
    expect(changeTone("rolling", 3)).toBe("bad");
    expect(changeTone("rolling", -3)).toBe("good");
    expect(changeTone("did_not_stop", 1)).toBe("bad");
    expect(changeTone("did_not_stop", -1)).toBe("good");
    expect(changeTone("indeterminate", 5)).toBe("neutral");
    expect(changeTone("stopped", 0)).toBe("neutral");
  });

  it("is said in words", () => {
    expect(pointsText(4)).toBe("up 4 points");
    expect(pointsText(1)).toBe("up 1 point");
    expect(pointsText(-1)).toBe("down 1 point");
    expect(pointsText(-12)).toBe("down 12 points");
    expect(pointsText(0)).toBe("no change");
  });
});

describe("the stop rate", () => {
  it("is the share of those that could be judged who stopped", () => {
    expect(stopRate(counts(8, 1, 1, 10))).toEqual({ percent: 80, of: 10 });
    expect(stopRate(counts(1, 2))).toEqual({ percent: 33, of: 3 });
  });

  it("is not there when no vehicle could be judged", () => {
    expect(stopRate(counts(0, 0, 0, 4))).toBeNull();
    expect(stopRate(counts())).toBeNull();
  });
});

describe("the stop rate of a column", () => {
  it("is a percentage of the vehicles that could be judged, or a dash", () => {
    expect(stopRateText({ stopped: 3, rolling: 1, did_not_stop: 0, indeterminate: 9 })).toBe("75%");
    expect(stopRateText({ stopped: 0, rolling: 2 })).toBe("0%");
    expect(stopRateText({ indeterminate: 4 })).toBe("–");
    expect(stopRateText({})).toBe("–");
  });
});

describe("the highest of a list", () => {
  it("goes to the earlier item on a tie, and there is none above zero when there is none", () => {
    const hours = [3, 9, 9, 2].map((n, hour) => ({ hour, n }));
    expect(peak(hours, (h) => h.n)).toEqual({ item: { hour: 1, n: 9 }, value: 9 });
    expect(peak([{ n: 0 }, { n: 0 }], (h) => h.n)).toBeNull();
    expect(peak([], (h: { n: number }) => h.n)).toBeNull();
  });
});

describe("the lowest and the highest stop rate", () => {
  const item = (name: string, stopped: number, rolling: number, did_not_stop = 0, indeterminate = 0) => ({ name, ...counts(stopped, rolling, did_not_stop, indeterminate) });

  it("are found among those with enough vehicles", () => {
    const result = rateExtremes([item("a", 9, 1), item("b", 3, 3, 1), item("c", 5, 0), item("tiny", 0, 2)]);
    expect(result?.lowest.item.name).toBe("b");
    expect(result?.lowest).toMatchObject({ percent: 43, of: 7 });
    expect(result?.highest?.item.name).toBe("c");
    expect(result?.highest).toMatchObject({ percent: 100, of: 5 });
  });

  it("need at least the minimum number of vehicles that could be judged, not counting those that could not", () => {
    expect(MIN_SAMPLE).toBe(5);
    expect(rateExtremes([item("a", 4, 0, 0, 30), item("b", 5, 0)])).toBeNull(); // a has only 4 judged
    expect(rateExtremes([item("a", 4, 1), item("b", 5, 0)])).not.toBeNull();
  });

  it("need two to compare", () => {
    expect(rateExtremes([item("a", 9, 1)])).toBeNull();
    expect(rateExtremes([])).toBeNull();
  });

  it("have no highest when they all stop at the same rate, and the earlier wins a tie for the lowest", () => {
    const same = rateExtremes([item("a", 5, 5), item("b", 5, 5)]);
    expect(same?.lowest.item.name).toBe("a");
    expect(same?.highest).toBeNull();
  });
});

describe("a speed in words", () => {
  it("follows the sizes a second and the units, for a car whose size is about 2.5 m", () => {
    expect(approximateSpeed(0.7, "mph")).toBe(4); // the default for a roll: 1.75 m a second
    expect(approximateSpeed(1, "mph")).toBe(6);
    expect(approximateSpeed(1, "kmh")).toBe(9);
    expect(approximateSpeed(2.5, "kmh")).toBe(23); // 6.25 m a second is 22.5 km/h, and a half goes up
  });

  it("says about, and almost still for nothing", () => {
    expect(speedText(0.7, "mph")).toBe("about 4 mph");
    expect(speedText(1, "kmh")).toBe("about 9 km/h");
    expect(speedText(0.04, "mph")).toBe("almost still");
    expect(speedText(0, "kmh")).toBe("almost still");
  });
});

describe("days", () => {
  it("are labelled by their own date, whatever the browser's zone is", () => {
    expect(dayLabel("2026-10-09")).toBe("Oct 9");
    expect(dayLabel("2026-01-01")).toBe("Jan 1");
    expect(dayTitle("2026-10-09")).toBe("Fri, Oct 9, 2026");
    expect(dayTitle("2026-12-31")).toBe("Thu, Dec 31, 2026");
  });

  it("are shown as they came if they are not dates", () => {
    expect(dayLabel("soon")).toBe("soon");
    expect(dayTitle("2026-13-45x")).toBe("2026-13-45x");
  });

  it("have weekdays that start on Monday, as the server counts", () => {
    expect([0, 4, 6].map(weekdayName)).toEqual(["Monday", "Friday", "Sunday"]);
    expect(weekdayName(9)).toBe("9");
  });

  it("make a range in words", () => {
    expect(rangeText("2026-10-03", "2026-10-09")).toBe("Oct 3 to Oct 9");
    expect(rangeText("2026-10-09", "2026-10-09")).toBe("Oct 9");
    expect(rangeText(undefined, "2026-10-09")).toBe("");
  });

  it("have an average over the days that are over, and today does not count while there are others", () => {
    expect(averagePerFinishedDay([{ total: 100 }, { total: 200 }, { total: 17 }])).toBe(150);
    expect(averagePerFinishedDay([{ total: 100 }, { total: 101 }, { total: 3 }])).toBe(101); // rounded
    expect(averagePerFinishedDay([{ total: 40 }])).toBeNull(); // only today
    expect(averagePerFinishedDay([{ total: 0 }, { total: 0 }, { total: 9 }])).toBeNull();
    expect(averagePerFinishedDay([])).toBeNull();
  });
});

describe("rows for the charts", () => {
  const hour = (h: number, stopped: number, rolling = 0) => ({ hour: h, ...counts(stopped, rolling) });

  it("have the outcomes stacked with the good one on the baseline, in the reserved colours", () => {
    expect(OUTCOME_SERIES.map((s) => [s.key, s.label])).toEqual([
      ["stopped", "Stopped"],
      ["rolling", "Rolled through"],
      ["did_not_stop", "Did not stop"],
      ["indeterminate", "Unclear"],
    ]);
    expect(OUTCOME_SERIES.map((s) => s.color)).toEqual([OUTCOME_COLOR.stopped, OUTCOME_COLOR.rolling, OUTCOME_COLOR.did_not_stop, OUTCOME_COLOR.indeterminate]);
    expect(OUTCOME_COLOR.stopped).toBe("var(--good)");
    expect(OUTCOME_COLOR.rolling).toBe("var(--warning)");
    expect(OUTCOME_COLOR.did_not_stop).toBe("var(--critical)");
  });

  it("give an hour its two digits on the axis and its range in the tooltip", () => {
    const [row] = outcomeHourRows([hour(7, 3, 2)]);
    expect(row).toEqual({
      id: "h7",
      label: "07",
      title: "07:00–08:00",
      values: { stopped: 3, rolling: 2, did_not_stop: 0, indeterminate: 0 },
      total: 5,
    });
  });

  it("give a day its date on the axis and in full in the tooltip", () => {
    const [row] = outcomeDayRows([{ date: "2026-10-09", t: 0, fastest: null, ...counts(1, 0, 4) }]);
    expect(row).toMatchObject({ id: "2026-10-09", label: "Oct 9", title: "Fri, Oct 9, 2026", total: 5 });
    expect(row?.values.did_not_stop).toBe(4);
  });

  it("make traffic rows by kind", () => {
    const [hourRow] = trafficHourRows([{ hour: 18, total: 8, by_class: { car: 6, truck: 2 } }]);
    expect(hourRow).toEqual({ id: "h18", label: "18", title: "18:00–19:00", values: { car: 6, truck: 2 }, total: 8 });
    const [dayRow] = trafficDayRows([{ date: "2026-10-08", t: 0, total: 3, by_class: { bus: 3 } }]);
    expect(dayRow).toMatchObject({ id: "2026-10-08", label: "Oct 8", values: { bus: 3 }, total: 3 });
  });

  it("stack the kinds that were counted, the known ones in their order and the others after, with their colours", () => {
    const series = classSeries([{ by_class: { truck: 2, car: 0 } }, { by_class: { zebra: 1, bus: 4, car: 0 } }]);
    expect(series.map((s) => s.key)).toEqual(["truck", "bus", "zebra"]);
    expect(series[0]).toEqual({ key: "truck", label: "Trucks", color: "var(--series-2)" });
    expect(series[2]?.color).toBe("var(--series-other)");
    expect(classSeries([])).toEqual([]);
  });

  it("turn into shares of their own total, and an empty row stays empty", () => {
    const rows = outcomeHourRows([hour(7, 3, 1), hour(8, 0)]);
    const [a, b] = asShares(rows);
    expect(a?.values).toMatchObject({ stopped: 75, rolling: 25, did_not_stop: 0 });
    expect(a?.total).toBe(100);
    expect(b?.values.stopped).toBe(0);
    expect(b?.total).toBe(0);
    expect(rows[0]?.values.stopped).toBe(3); // the rows given are not changed
  });
});
