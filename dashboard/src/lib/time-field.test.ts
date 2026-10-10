import { describe, expect, it } from "vitest";
import { isTime, normalizeTime } from "./time-field";

describe("a clock time typed in 24 hours", () => {
  it("tidies what people type into HH:MM", () => {
    expect(normalizeTime("9")).toBe("09:00");
    expect(normalizeTime("18")).toBe("18:00");
    expect(normalizeTime("930")).toBe("09:30");
    expect(normalizeTime("1830")).toBe("18:30");
    expect(normalizeTime("9:30")).toBe("09:30");
    expect(normalizeTime("18:05")).toBe("18:05");
    expect(normalizeTime(" 7 ")).toBe("07:00");
    expect(normalizeTime("00:00")).toBe("00:00");
    expect(normalizeTime("23:59")).toBe("23:59");
  });

  it("refuses what is not a time of day", () => {
    for (const text of ["", "abc", "24:00", "9:75", "0:5", "12345", "9:30:00", "-1", "1 30", "7pm"]) {
      expect(normalizeTime(text), text).toBeNull();
    }
  });

  it("knows the canonical form", () => {
    expect(isTime("07:00")).toBe(true);
    expect(isTime("23:59")).toBe(true);
    expect(isTime("7:00")).toBe(false);
    expect(isTime("24:00")).toBe(false);
    expect(isTime("")).toBe(false);
  });
});
