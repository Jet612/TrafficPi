import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { ESTIMATE_CAVEAT, MEASURED_CAVEAT, aKind, fastestText, inUnits, measuredSpeed, roughSpeed, speedPhrase, speedShort, SIZE_METRES } from "./speed";

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
  it("names a kind with its article, and a vehicle for one it has no word for", () => {
    expect(aKind("car")).toBe("a car");
    expect(aKind("bus")).toBe("a bus");
    expect(aKind("boat")).toBe("a vehicle");
  });

  it("tells when on the camera's clock, as a moment reads everywhere else", () => {
    const fastest = { speed_ms: 13.5, label: "bus", at: Date.UTC(2026, 9, 9, 21, 42), calibrated: false }; // 17:42 in New York
    expect(fastestText(fastest, "mph", "UTC", Date.UTC(2026, 9, 9, 22, 42)).note).toBe("a bus at 21:42"); // the same day
    expect(fastestText(fastest, "mph", "UTC", Date.UTC(2026, 9, 10, 9)).note).toBe("a bus yesterday at 21:42");
    expect(fastestText(fastest, "mph", "Asia/Kolkata", Date.UTC(2026, 9, 12)).note).toBe("a bus on Sat, Oct 10 at 03:12"); // the next morning there
    expect(fastestText(fastest, "mph", "UTC", Date.UTC(2026, 9, 9, 21, 52)).note).toBe("a bus 10 min ago");
    expect(fastestText(fastest, "mph", "UTC", Date.UTC(2027, 0, 5)).note).toBe("a bus on Fri, Oct 9, 2026 at 21:42"); // another year says its year
    expect(fastestText({ ...fastest, label: "boat" }, "mph", "Mars/Olympus", Date.UTC(2026, 9, 12)).note).toBe("a vehicle on Fri, Oct 9 at 21:42"); // a zone nobody knows is UTC
  });

  it("makes a headline and a note of the fastest estimate", () => {
    const fastest = { speed_ms: 13.5, label: "car", at: Date.UTC(2026, 9, 9, 21, 42), calibrated: false };
    const later = Date.UTC(2026, 9, 12, 12); // three days on
    expect(fastestText(fastest, "mph", "America/New_York", later)).toEqual({ speed: "about 30 mph", note: "a car on Fri, Oct 9 at 17:42" });
    expect(fastestText(fastest, "kmh", "America/New_York", later).speed).toBe("about 50 km/h");
  });

  it("says a speed measured on a calibrated road without an about, and to the whole mile", () => {
    const measured = { speed_ms: 12.5, label: "truck", at: Date.UTC(2026, 9, 9, 21, 42), calibrated: true };
    const later = Date.UTC(2026, 9, 12, 12);
    expect(fastestText(measured, "mph", "America/New_York", later)).toEqual({ speed: "28 mph", note: "a truck on Fri, Oct 9 at 17:42" });
    expect(fastestText(measured, "kmh", "America/New_York", later).speed).toBe("45 km/h");
  });
});
