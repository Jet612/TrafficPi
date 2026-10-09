import { describe, expect, it } from "vitest";
import { MAX_EVIDENCE_MB, MAX_KEEP_DAYS, MIN_EVIDENCE_MB, fromDraft, isChanged, parseEvidenceMb, parseKeepDays, roughly, sizeText, toDraft, zoneChoices } from "./settings";
import type { Settings } from "./types";

const saved: Settings = {
  timezone: "America/New_York",
  units: "mph",
  trails_enabled: true,
  trails_people: false,
  trails_keep_days: 365,
  evidence_enabled: true,
  evidence_keep_days: 30,
  evidence_max_mb: 2048,
};

describe("the settings form", () => {
  it("shows what is saved, and sends it back as it was", () => {
    const draft = toDraft(saved);
    expect(draft).toEqual({
      timezone: "America/New_York",
      units: "mph",
      trailsEnabled: true,
      trailsPeople: false,
      keepDays: "365",
      evidenceEnabled: true,
      evidenceDays: "30",
      evidenceMb: "2048",
    });
    expect(fromDraft(draft)).toEqual({ ok: true, settings: saved });
  });

  it("shows the evidence settings as they are saved, whatever they are", () => {
    const other: Settings = { ...saved, evidence_enabled: false, evidence_keep_days: 7, evidence_max_mb: 500 };
    expect(toDraft(other)).toMatchObject({ evidenceEnabled: false, evidenceDays: "7", evidenceMb: "500" });
    expect(fromDraft(toDraft(other))).toEqual({ ok: true, settings: other });
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
    expect(fromDraft({ ...toDraft(saved), keepDays: "forever" })).toEqual({ ok: false, problem: "Enter a whole number of days from 1 to 3650 to keep trails." });
    expect(fromDraft({ ...toDraft(saved), evidenceDays: "0" })).toEqual({ ok: false, problem: "Enter a whole number of days from 1 to 3650 to keep pictures and clips." });
    expect(fromDraft({ ...toDraft(saved), evidenceMb: "10" })).toEqual({
      ok: false,
      problem: "Enter a whole number of megabytes from 50 to 1000000 for pictures and clips.",
    });
  });

  it("trims the time zone and gives every member, as the server insists", () => {
    const result = fromDraft({
      ...toDraft(saved),
      timezone: " Europe/Paris ",
      units: "kmh",
      trailsEnabled: false,
      trailsPeople: true,
      keepDays: "30",
      evidenceEnabled: false,
      evidenceDays: " 7",
      evidenceMb: "500 ",
    });
    expect(result).toEqual({
      ok: true,
      settings: {
        timezone: "Europe/Paris",
        units: "kmh",
        trails_enabled: false,
        trails_people: true,
        trails_keep_days: 30,
        evidence_enabled: false,
        evidence_keep_days: 7,
        evidence_max_mb: 500,
      },
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
    expect(isChanged({ ...toDraft(saved), evidenceEnabled: false }, saved)).toBe(true);
    expect(isChanged({ ...toDraft(saved), evidenceDays: "31" }, saved)).toBe(true);
    expect(isChanged({ ...toDraft(saved), evidenceDays: " 30 " }, saved)).toBe(false);
    expect(isChanged({ ...toDraft(saved), evidenceMb: "2049" }, saved)).toBe(true);
    expect(isChanged({ ...toDraft(saved), evidenceMb: " 2048" }, saved)).toBe(false);
  });
});

describe("the room for evidence", () => {
  it("takes only a whole number of megabytes the server will take", () => {
    expect(parseEvidenceMb("2048")).toBe(2048);
    expect(parseEvidenceMb(" 50 ")).toBe(MIN_EVIDENCE_MB);
    expect(parseEvidenceMb(String(MAX_EVIDENCE_MB))).toBe(MAX_EVIDENCE_MB);
    for (const bad of ["", " ", "49", "0", "-100", "1.5", "100.5", "1000.0", "2e3", "2 GB", "1000001", "99999999", "0x100", "٢٠٤٨"]) {
      expect(parseEvidenceMb(bad), bad).toBeNull();
    }
  });

  it("reads megabytes at a glance", () => {
    expect(sizeText(50)).toBe("50 MB");
    expect(sizeText(999)).toBe("999 MB");
    expect(sizeText(1000)).toBe("1 GB");
    expect(sizeText(1500)).toBe("1.5 GB");
    expect(sizeText(1590)).toBe("1.6 GB"); // rounded, not cut off
    expect(sizeText(2048)).toBe("2 GB");
    expect(sizeText(10_000)).toBe("10 GB");
    expect(sizeText(MAX_EVIDENCE_MB)).toBe("1000 GB");
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
