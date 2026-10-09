import { describe, expect, it } from "vitest";
import { MAX_KEEP_DAYS, fromDraft, isChanged, parseKeepDays, roughly, toDraft, zoneChoices } from "./settings";
import type { Settings } from "./types";

const saved: Settings = { timezone: "America/New_York", units: "mph", trails_enabled: true, trails_people: false, trails_keep_days: 365 };

describe("the settings form", () => {
  it("shows what is saved, and sends it back as it was", () => {
    const draft = toDraft(saved);
    expect(draft).toEqual({ timezone: "America/New_York", units: "mph", trailsEnabled: true, trailsPeople: false, keepDays: "365" });
    expect(fromDraft(draft)).toEqual({ ok: true, settings: saved });
  });

  it("takes only a whole number of days the server will take", () => {
    expect(parseKeepDays("30")).toBe(30);
    expect(parseKeepDays(" 30 ")).toBe(30);
    expect(parseKeepDays("1")).toBe(1);
    expect(parseKeepDays(String(MAX_KEEP_DAYS))).toBe(MAX_KEEP_DAYS);
    for (const bad of ["", " ", "0", "-5", "1.5", "1e3", "a year", "3651", "99999", "100000", "0x10", "٣٠"]) {
      expect(parseKeepDays(bad), bad).toBeNull();
    }
  });

  it("says what is wrong, in words", () => {
    expect(fromDraft({ ...toDraft(saved), timezone: "  " })).toEqual({ ok: false, problem: "Enter a time zone, like America/New_York." });
    expect(fromDraft({ ...toDraft(saved), keepDays: "forever" })).toEqual({ ok: false, problem: "Enter a whole number of days from 1 to 3650." });
  });

  it("trims the time zone and gives every member, as the server insists", () => {
    const result = fromDraft({ ...toDraft(saved), timezone: " Europe/Paris ", units: "kmh", trailsEnabled: false, trailsPeople: true, keepDays: "30" });
    expect(result).toEqual({
      ok: true,
      settings: { timezone: "Europe/Paris", units: "kmh", trails_enabled: false, trails_people: true, trails_keep_days: 30 },
    });
  });

  it("knows when something has changed, and when it only looks it", () => {
    expect(isChanged(toDraft(saved), saved)).toBe(false);
    expect(isChanged({ ...toDraft(saved), timezone: " America/New_York " }, saved)).toBe(false);
    expect(isChanged({ ...toDraft(saved), keepDays: " 365" }, saved)).toBe(false);
    expect(isChanged({ ...toDraft(saved), keepDays: "366" }, saved)).toBe(true);
    expect(isChanged({ ...toDraft(saved), units: "kmh" }, saved)).toBe(true);
    expect(isChanged({ ...toDraft(saved), trailsPeople: true }, saved)).toBe(true);
    expect(isChanged({ ...toDraft(saved), trailsEnabled: false }, saved)).toBe(true);
    expect(isChanged({ ...toDraft(saved), timezone: "UTC" }, saved)).toBe(true);
    expect(isChanged({ ...toDraft(saved), keepDays: "" }, saved)).toBe(true); // half typed is a change, though it cannot be saved
  });
});

describe("time zone suggestions", () => {
  it("add UTC and the zone in use, once each, in order", () => {
    expect(zoneChoices(["Europe/Paris", "America/New_York"], "Asia/Tokyo")).toEqual(["America/New_York", "Asia/Tokyo", "Europe/Paris", "UTC"]);
    expect(zoneChoices(["UTC", "Europe/Paris"], "UTC")).toEqual(["Europe/Paris", "UTC"]);
    expect(zoneChoices([], "")).toEqual(["UTC"]);
  });
});

describe("how long, roughly", () => {
  it("reads a number of days at a glance", () => {
    expect(roughly(1)).toBe("1 day");
    expect(roughly(30)).toBe("30 days");
    expect(roughly(59)).toBe("59 days");
    expect(roughly(60)).toBe("about 2 months");
    expect(roughly(180)).toBe("about 6 months");
    expect(roughly(329)).toBe("about 11 months");
    expect(roughly(330)).toBe("about 1 year");
    expect(roughly(365)).toBe("about 1 year");
    expect(roughly(730)).toBe("about 2 years");
    expect(roughly(1000)).toBe("about 2.5 years");
    expect(roughly(3650)).toBe("about 10 years");
  });
});
