import { describe, expect, it } from "vitest";
import { coverageText, speedBinRows, speedValue } from "./speeds";

describe("the histogram's columns", () => {
  const bins = [
    { low: 0, high: 5, count: 2 },
    { low: 5, high: 10, count: 0 },
    { low: 10, high: 15, count: 7 },
  ];

  it("have the start of the bar on the axis and both ends in the tooltip and the table", () => {
    expect(speedBinRows(bins, "mph")).toEqual([
      { id: "bin-0", label: "0", title: "0 to 5 mph", values: { vehicles: 2 }, total: 2 },
      { id: "bin-5", label: "5", title: "5 to 10 mph", values: { vehicles: 0 }, total: 0 },
      { id: "bin-10", label: "10", title: "10 to 15 mph", values: { vehicles: 7 }, total: 7 },
    ]);
  });

  it("are in the units that are set", () => {
    expect(speedBinRows(bins, "kmh")[2]!.title).toBe("10 to 15 km/h");
  });

  it("are none when nothing was measured", () => {
    expect(speedBinRows([], "mph")).toEqual([]);
  });
});

describe("a speed that was measured", () => {
  it("is a whole number with its unit, and no about", () => {
    expect(speedValue(27.6, "mph")).toBe("28 mph");
    expect(speedValue(45.4, "kmh")).toBe("45 km/h");
    expect(speedValue(0.4, "mph")).toBe("0 mph");
  });
});

describe("how many vehicles had a speed", () => {
  const figures = (count: number) => ({ count, mean: 20, median: 20, p85: 25, fastest: 30, slowest: 10 });

  it("says how many of those counted, and the share", () => {
    expect(coverageText({ counted: 120, figures: figures(90) })).toBe("90 of the 120 vehicles counted had a speed (75%).");
  });

  it("says when none had", () => {
    expect(coverageText({ counted: 40, figures: null })).toBe("None of the 40 vehicles counted had a speed.");
  });

  it("says when none was counted, or when a speed has no count to go with it", () => {
    expect(coverageText({ counted: 0, figures: null })).toBe("No vehicle was counted.");
    expect(coverageText({ counted: 0, figures: figures(3) })).toBe("3 had a speed.");
  });
});
