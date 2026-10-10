import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ESTIMATE_CAVEAT, MEASURED_CAVEAT, aKind, fastestText, inUnits, measuredSpeed, passedText, roughSpeed, speedPhrase, speedShort, SIZE_METRES } from "./speed";

const cases = JSON.parse(readFileSync(new URL("../../../backend/tests/data/speed_cases.json", import.meta.url), "utf8")) as {
  rough: { value: number; rough: number }[];
  phrases: { speed_ms: number; units: "mph" | "kmh"; text: string }[];
  caveats: { estimate: string; measured: string };
  measured: { value: number; whole: number }[];
  measured_phrases: { speed_ms: number; units: "mph" | "kmh"; text: string }[];
};

describe("a speed in words, the same as the server says it", () => {
  it.each(cases.rough)("rounds $value to $rough", ({ value, rough }) => {
    expect(roughSpeed(value)).toBe(rough);
  });

  it.each(cases.phrases)("says $speed_ms m/s in $units as $text", ({ speed_ms, units, text }) => {
    expect(speedPhrase(speed_ms, units)).toBe(text);
  });

  it.each(cases.phrases)("says it short in a table: $speed_ms m/s in $units", ({ speed_ms, units, text }) => {
    expect(speedShort(speed_ms, units)).toBe(text.replace(/^about /, ""));
  });

  it.each(cases.measured)("rounds the measured $value to the whole $whole", ({ value, whole }) => {
    expect(measuredSpeed(value)).toBe(whole);
  });

  it.each(cases.measured_phrases)("says a measured $speed_ms m/s in $units as $text", ({ speed_ms, units, text }) => {
    expect(speedPhrase(speed_ms, units, true)).toBe(text);
    expect(speedShort(speed_ms, units, true)).toBe(text);
  });

  it("keeps an estimate an about unless it is said to be measured", () => {
    expect(speedPhrase(13.411, "mph")).toBe("about 30 mph");
    expect(speedPhrase(13.411, "mph", false)).toBe("about 30 mph");
    expect(speedPhrase(13.411, "mph", true)).toBe("30 mph");
  });

  it("never goes backwards, estimated or measured", () => {
    for (let i = 1; i < 1500; i++) {
      expect(measuredSpeed(i / 10)).toBeGreaterThanOrEqual(measuredSpeed((i - 1) / 10));
    }
    let last = -1;
    for (let i = 0; i < 1500; i++) {
      const rough = roughSpeed(i / 10);
      expect(rough).toBeGreaterThanOrEqual(last);
      last = rough;
    }
  });
});

describe("what a speed is worth", () => {
  it("is said in the server's words, estimate and measured alike", () => {
    expect(ESTIMATE_CAVEAT).toBe(cases.caveats.estimate);
    expect(MEASURED_CAVEAT).toBe(cases.caveats.measured);
  });
});

describe("units", () => {
  it("turn metres a second into miles or kilometres an hour", () => {
    expect(inUnits(10, "mph")).toBeCloseTo(22.37, 2);
    expect(inUnits(10, "kmh")).toBeCloseTo(36, 6);
  });

  it("take a size to be about what a car looks like in the picture, not its length", () => {
    expect(SIZE_METRES).toBeGreaterThanOrEqual(1.6);
    expect(SIZE_METRES).toBeLessThanOrEqual(2.6);
  });
});

describe("what was passed", () => {
  it("is told on the camera's clock", () => {
    const at = Date.UTC(2026, 9, 9, 21, 42); // 17:42 in New York
    expect(passedText(at, "America/New_York")).toBe("Fri 9 Oct, 17:42");
    expect(passedText(at, "UTC")).toBe("Fri 9 Oct, 21:42");
    expect(passedText(at, "Asia/Kolkata")).toBe("Sat 10 Oct, 03:12");
  });

  it("falls back to UTC for a zone nobody knows", () => {
    expect(passedText(Date.UTC(2026, 9, 9, 21, 42), "Mars/Olympus")).toBe("Fri 9 Oct, 21:42");
  });

  it("names a kind with its article, and a vehicle for one it has no word for", () => {
    expect(aKind("car")).toBe("a car");
    expect(aKind("bus")).toBe("a bus");
    expect(aKind("boat")).toBe("a vehicle");
  });

  it("makes a headline and a note of the fastest estimate", () => {
    const fastest = { speed_ms: 13.5, label: "car", at: Date.UTC(2026, 9, 9, 21, 42), calibrated: false };
    expect(fastestText(fastest, "mph", "America/New_York")).toEqual({ speed: "about 30 mph", note: "a car, Fri 9 Oct, 17:42" });
    expect(fastestText(fastest, "kmh", "America/New_York").speed).toBe("about 50 km/h");
  });

  it("says a speed measured on a calibrated road without an about, and to the whole mile", () => {
    const measured = { speed_ms: 12.5, label: "truck", at: Date.UTC(2026, 9, 9, 21, 42), calibrated: true };
    expect(fastestText(measured, "mph", "America/New_York")).toEqual({ speed: "28 mph", note: "a truck, Fri 9 Oct, 17:42" });
    expect(fastestText(measured, "kmh", "America/New_York").speed).toBe("45 km/h");
  });
});
