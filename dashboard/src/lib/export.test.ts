import { describe, expect, it } from "vitest";
import { exportUrl } from "./export";

describe("the files to download", () => {
  it("name the kind and the number of days, and all time is 0", () => {
    expect(exportUrl("counts", 7)).toBe("/api/export/counts.csv?days=7");
    expect(exportUrl("passes", 90)).toBe("/api/export/passes.csv?days=90");
    expect(exportUrl("counts", 0)).toBe("/api/export/counts.csv?days=0");
  });
});
