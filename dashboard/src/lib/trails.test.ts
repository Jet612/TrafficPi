import { describe, expect, it } from "vitest";
import { lineOf, profileOf, rectangle, stopOf, zoneOf } from "./fixtures";
import {
  allDay,
  profileScene,
  clampSpan,
  countIn,
  countText,
  dayTitle,
  evenSample,
  hourButtonLabel,
  hourCounts,
  isAllDay,
  isDay,
  kindTotals,
  kindsIn,
  nextHour,
  noun,
  pathsOfKinds,
  pictureFileName,
  playlist,
  sameSpan,
  shiftDay,
  shownKinds,
  spanBetween,
  spanLabel,
  spanRange,
} from "./trails";
import type { TrailDay, TrailHour, TrailPath } from "./types";

const HOUR = 3_600_000;
const MIDNIGHT = 1_760_000_000_000;

/** A day of `hours` hours starting at local `startHour`, with the given counts by kind. */
function dayOf(byHour: Record<number, Record<string, number>> = {}, hours = 24, repeat?: number): TrailDay {
  const list: TrailHour[] = [];
  let clock = 0;
  for (let index = 0; index < hours; index++) {
    const hour = clock;
    if (repeat !== undefined && index === repeat) {
      // the clocks go back: this hour shows the same local hour as the one before
      list.push(entry(index, list[index - 1]!.hour, byHour[index] ?? {}));
      continue;
    }
    list.push(entry(index, hour, byHour[index] ?? {}));
    clock++;
  }
  const total = list.reduce((sum, h) => sum + h.count, 0);
  return { day: "2026-10-09", timezone: "UTC", total, first_day: "2026-10-01", last_day: "2026-10-09", hours: list };
}

function entry(index: number, hour: number, by_kind: Record<string, number>): TrailHour {
  const count = Object.values(by_kind).reduce((a, b) => a + b, 0);
  return { t: MIDNIGHT + index * HOUR, end: MIDNIGHT + (index + 1) * HOUR, hour, count, by_kind };
}

function path(label: string): TrailPath {
  return { id: 1, started_at: 0, ended_at: 1, label, x: [0, 1], y: [0, 1] };
}

describe("hour spans", () => {
  const day = dayOf();

  it("covers the whole day, or one hour, or a run from either end", () => {
    expect(allDay(day)).toEqual({ from: 0, to: 23 });
    expect(spanBetween(7, 3)).toEqual({ from: 3, to: 7 });
    expect(spanBetween(3, 7)).toEqual({ from: 3, to: 7 });
    expect(isAllDay(day, { from: 0, to: 23 })).toBe(true);
    expect(isAllDay(day, { from: 0, to: 22 })).toBe(false);
    expect(sameSpan({ from: 1, to: 2 }, { from: 1, to: 2 })).toBe(true);
    expect(sameSpan({ from: 1, to: 2 }, { from: 1, to: 3 })).toBe(false);
  });

  it("keeps a span inside a day that has fewer hours", () => {
    const short = dayOf({}, 23);
    expect(clampSpan({ from: 20, to: 24 }, short)).toEqual({ from: 20, to: 22 });
    expect(clampSpan({ from: 23, to: 23 }, short)).toEqual({ from: 22, to: 22 });
    expect(clampSpan({ from: -3, to: 5 }, short)).toEqual({ from: 0, to: 5 });
    expect(allDay(short)).toEqual({ from: 0, to: 22 });
  });

  it("asks for the time from the start of the first hour to the end of the last", () => {
    expect(spanRange(day, { from: 2, to: 4 })).toEqual({ fromMs: MIDNIGHT + 2 * HOUR, toMs: MIDNIGHT + 5 * HOUR });
    expect(spanRange(day, allDay(day))).toEqual({ fromMs: MIDNIGHT, toMs: MIDNIGHT + 24 * HOUR });
    expect(spanRange(day, { from: 5, to: 40 })).toBeNull();
  });

  it("ends a last hour that is short where the day ends", () => {
    const odd = dayOf({}, 2);
    odd.hours[1] = { ...odd.hours[1]!, end: odd.hours[1]!.t + HOUR / 2 };
    expect(spanRange(odd, { from: 0, to: 1 })).toEqual({ fromMs: MIDNIGHT, toMs: MIDNIGHT + 1.5 * HOUR });
  });

  it("names a span for people to read", () => {
    expect(spanLabel(day, allDay(day))).toBe("All day");
    expect(spanLabel(day, { from: 8, to: 8 })).toBe("08:00–09:00");
    expect(spanLabel(day, { from: 7, to: 9 })).toBe("07:00–10:00");
    expect(spanLabel(day, { from: 22, to: 23 })).toBe("22:00–00:00");
    expect(spanLabel(day, { from: 5, to: 99 })).toBe("");
  });
});

describe("hour buttons", () => {
  it("say the time and how many trails began in it", () => {
    const day = dayOf();
    const cars = new Set(["car"]);
    expect(hourButtonLabel(day, 8, 123, cars)).toBe("08:00–09:00: 123 vehicles");
    expect(hourButtonLabel(day, 9, 0, cars)).toBe("09:00–10:00: no vehicles");
    expect(hourButtonLabel(day, 10, 1, cars)).toBe("10:00–11:00: 1 vehicle");
    expect(hourButtonLabel(day, 11, 2, new Set(["person"]))).toBe("11:00–12:00: 2 people");
    expect(hourButtonLabel(day, 40, 1, cars)).toBe("");
  });

  it("tell the hour that happens twice apart", () => {
    const day = dayOf({}, 25, 2); // 00, 01, 01, 02, ...
    expect(day.hours.map((h) => h.hour).slice(0, 4)).toEqual([0, 1, 1, 2]);
    const cars = new Set(["car"]);
    expect(hourButtonLabel(day, 1, 4, cars)).toBe("01:00–02:00, the first time: 4 vehicles");
    expect(hourButtonLabel(day, 2, 1, cars)).toBe("01:00–02:00, the second time: 1 vehicle");
    expect(hourButtonLabel(day, 3, 2, cars)).toBe("02:00–03:00: 2 vehicles");
  });
});

describe("kinds", () => {
  const day = dayOf({ 7: { truck: 2, car: 10 }, 8: { car: 30, person: 3 }, 9: { bus: 0 } });

  it("lists those present, in the fixed order, and ignores those with a count of nothing", () => {
    expect(kindsIn(day)).toEqual(["car", "truck", "person"]);
    expect(kindsIn(dayOf())).toEqual([]);
  });

  it("puts a kind the dashboard does not know after the known ones", () => {
    expect(kindsIn(dayOf({ 1: { scooter: 1, bus: 1, car: 1 } }))).toEqual(["car", "bus", "scooter"]);
  });

  it("shows what is present and not switched off", () => {
    expect([...shownKinds(day, new Set(["truck"]))]).toEqual(["car", "person"]);
    expect([...shownKinds(day, new Set(["dog"]))]).toEqual(["car", "truck", "person"]);
  });

  it("counts by hour for the shown kinds only", () => {
    const counts = hourCounts(day, new Set(["car", "person"]));
    expect(counts.slice(6, 10)).toEqual([0, 10, 33, 0]);
    expect(hourCounts(day, new Set()).every((n) => n === 0)).toBe(true);
  });

  it("totals the day by kind, or a span of it", () => {
    expect(Object.fromEntries(kindTotals(day))).toEqual({ truck: 2, car: 40, person: 3, bus: 0 });
    expect(Object.fromEntries(kindTotals(day, { from: 8, to: 8 }))).toEqual({ car: 30, person: 3 });
    expect(Object.fromEntries(kindTotals(day, { from: 7, to: 8 })).car).toBe(40);
  });

  it("adds up a span", () => {
    const counts = hourCounts(day, new Set(["car", "truck", "person"]));
    expect(countIn(counts, { from: 7, to: 8 })).toBe(45);
    expect(countIn(counts, { from: 0, to: 23 })).toBe(45);
    expect(countIn(counts, { from: 20, to: 30 })).toBe(0);
  });

  it("keeps the paths of the shown kinds", () => {
    const paths = [path("car"), path("truck"), path("car"), path("person")];
    expect(pathsOfKinds(paths, new Set(["car", "person"])).map((p) => p.label)).toEqual(["car", "car", "person"]);
    expect(pathsOfKinds(paths, new Set())).toEqual([]);
  });
});

describe("playing a day", () => {
  it("stops at the hours that have something and then ends", () => {
    const list = playlist([0, 0, 3, 0, 0, 5, 1, 0]);
    expect(list).toEqual([2, 5, 6]);
    expect(nextHour(list, null)).toBe(2);
    expect(nextHour(list, 2)).toBe(5);
    expect(nextHour(list, 4)).toBe(5); // the hour on screen need not be in the list
    expect(nextHour(list, 6)).toBeNull();
    expect(nextHour([], null)).toBeNull();
  });
});

describe("words", () => {
  const format = (n: number) => n.toLocaleString("en-US");

  it("calls what is shown vehicles, people, or road users", () => {
    expect(noun(new Set(["car", "truck"]), true)).toBe("vehicles");
    expect(noun(new Set(["car"]), false)).toBe("vehicle");
    expect(noun(new Set(["person"]), true)).toBe("people");
    expect(noun(new Set(["person"]), false)).toBe("person");
    expect(noun(new Set(["car", "person"]), true)).toBe("road users");
    expect(noun(new Set(), true)).toBe("vehicles");
  });

  it("counts, and says so when only a sample is drawn", () => {
    const cars = new Set(["car"]);
    expect(countText(1, 1, cars, format)).toBe("1 vehicle");
    expect(countText(312, 312, cars, format)).toBe("312 vehicles");
    expect(countText(12_043, 5_000, cars, format)).toBe("12,043 vehicles (drawing an even sample of 5,000)");
    expect(countText(0, 0, cars, format)).toBe("0 vehicles");
  });

  it("takes an even sample of a list, the first and then every n-th", () => {
    const items = Array.from({ length: 10 }, (_, index) => index);
    expect(evenSample(items, 20)).toEqual(items);
    expect(evenSample(items, 10)).toEqual(items);
    expect(evenSample(items, 5)).toEqual([0, 2, 4, 6, 8]);
    expect(evenSample(items, 3)).toEqual([0, 3, 6]);
    expect(evenSample(items, 1)).toEqual([0]);
    expect(evenSample(items, 0)).toEqual([]);
    expect(evenSample([], 5)).toEqual([]);
    const many = Array.from({ length: 4_799 }, (_, index) => index);
    const sample = evenSample(many, 600);
    expect(sample).toHaveLength(600);
    expect(sample[0]).toBe(0);
    expect(sample.at(-1)! > 4_700).toBe(true); // reaches the end of the list
    expect(new Set(sample).size).toBe(600);
  });
});

describe("days", () => {
  it("knows a real date from text that only looks like one", () => {
    expect(isDay("2026-10-09")).toBe(true);
    expect(isDay("2028-02-29")).toBe(true);
    expect(isDay("2026-02-29")).toBe(false);
    expect(isDay("2026-13-01")).toBe(false);
    expect(isDay("2026-1-9")).toBe(false);
    expect(isDay("")).toBe(false);
    expect(isDay("yesterday")).toBe(false);
  });

  it("moves by days across months, years and leap days", () => {
    expect(shiftDay("2026-10-09", 1)).toBe("2026-10-10");
    expect(shiftDay("2026-10-31", 1)).toBe("2026-11-01");
    expect(shiftDay("2026-01-01", -1)).toBe("2025-12-31");
    expect(shiftDay("2028-03-01", -1)).toBe("2028-02-29");
    expect(shiftDay("2026-03-01", -1)).toBe("2026-02-28");
    expect(shiftDay("2026-10-09", 0)).toBe("2026-10-09");
    expect(shiftDay("nonsense", 1)).toBeNull();
  });

  it("writes a date out, the same one wherever the browser is", () => {
    expect(dayTitle("2026-10-09")).toBe("Friday 9 October 2026");
    expect(dayTitle("2026-01-01")).toBe("Thursday 1 January 2026");
    expect(dayTitle("nonsense")).toBe("nonsense");
  });
});

describe("saved pictures", () => {
  const day = dayOf();

  it("are named for what they show", () => {
    expect(pictureFileName(day, allDay(day), "paths")).toBe("trafficpi-paths-2026-10-09.png");
    expect(pictureFileName(day, allDay(day), "heat")).toBe("trafficpi-heat-map-2026-10-09.png");
    expect(pictureFileName(day, { from: 7, to: 8 }, "paths")).toBe("trafficpi-paths-2026-10-09-0700-0900.png");
    expect(pictureFileName(day, { from: 23, to: 23 }, "heat")).toBe("trafficpi-heat-map-2026-10-09-2300-0000.png");
  });
});

describe("the lines and zones of a profile", () => {
  it("become what the overlay draws, the two kinds of zone told apart", () => {
    const scene = profileScene(
      profileOf({
        lines: [lineOf("n", { name: "North", a: [0.1, 0.5], b: [0.9, 0.5] })],
        detection: [zoneOf("road", [[0, 0], [1, 0], [1, 1]], "Road")],
        exclusion: [zoneOf("lot", [[0, 0], [0.2, 0], [0.2, 0.2]], "Parking")],
      }),
    );
    expect(scene.lines).toEqual([{ id: "n", name: "North", a: [0.1, 0.5], b: [0.9, 0.5] }]);
    expect(scene.zones.map((zone) => [zone.id, zone.kind])).toEqual([["road", "detection"], ["lot", "exclusion"]]);
    expect(scene.stopSigns).toEqual([]);
  });

  it("carry the stop signs, with the way through", () => {
    const scene = profileScene(profileOf({ stops: [stopOf("elm", { name: "Elm", direction: -1 })] }));
    expect(scene.stopSigns).toEqual([
      { id: "elm", name: "Elm", approach: rectangle(0.15, 0.45, 0.5, 0.75), a: [0.5, 0.75], b: [0.5, 0.45], direction: -1 },
    ]);
  });
});
