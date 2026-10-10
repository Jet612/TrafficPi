import { describe, expect, it } from "vitest";
import { BACKUP_STALE_MS, acceleratorFacts, backupLevel, backupText, chipName, dataFacts, latencyText, memoryTestText } from "./machine";

const accelerator = { architecture: "HAILO8L", firmware: "4.23.0", model: "yolov8s_h8l", inference_ms: 17.43, inference_p95_ms: 17.91 };

describe("the accelerator on the status page", () => {
  it("names the chip as people know it", () => {
    expect(chipName("HAILO8L")).toBe("Hailo-8L");
    expect(chipName("HAILO8")).toBe("Hailo-8");
    expect(chipName("hailo10h")).toBe("hailo10h");
    expect(chipName(null)).toBe("unknown");
    expect(chipName(undefined)).toBe("unknown");
  });

  it("lists what else is known of it, and nothing it does not know", () => {
    expect(acceleratorFacts(accelerator)).toEqual([
      { label: "Model", value: "yolov8s_h8l" },
      { label: "Firmware", value: "4.23.0" },
    ]);
    expect(acceleratorFacts({ ...accelerator, firmware: null })).toEqual([{ label: "Model", value: "yolov8s_h8l" }]);
    expect(acceleratorFacts({})).toEqual([]);
  });

  it("gives how long a picture takes, if it has been timed", () => {
    expect(latencyText(accelerator)).toEqual({ value: "17.4 ms", note: "95% under 17.9 ms" });
    expect(latencyText({ ...accelerator, inference_p95_ms: null })).toEqual({ value: "17.4 ms", note: null });
    expect(latencyText({ architecture: "HAILO8L" })).toBeNull();
  });
});

describe("the data directory", () => {
  it("lists the database, the evidence and the backups, and how many counts are stored", () => {
    expect(dataFacts({ database_mb: 0.4, evidence_mb: 35.2, backups_mb: 3.2, evidence_files: 112, backup_files: 7 }, 3102)).toEqual([
      { label: "Database", value: "0.4 MB" },
      { label: "Pictures and clips", value: "35 MB (112 files)" },
      { label: "Backups", value: "3.2 MB (7)" },
      { label: "Counts stored", value: "3,102" },
    ]);
    expect(dataFacts({ database_mb: 1234, evidence_mb: 0, backups_mb: 0, evidence_files: 1, backup_files: 0 }, 0).map((fact) => fact.value)).toEqual([
      "1.2 GB",
      "0 MB (1 file)",
      "0 MB (0)",
      "0",
    ]);
  });
});

describe("the last backup", () => {
  const backup = { at_ms: Date.UTC(2026, 9, 10, 7, 30), size_mb: 0.437, count: 7 };

  it("says when, how big and how many, in the camera's time zone", () => {
    const the11th = Date.UTC(2026, 9, 11, 6, 0); // 02:00 on the 11th in New York: the backup was yesterday
    expect(backupText(backup, "America/New_York", the11th)).toEqual({ value: "Yesterday, 03:30", note: "0.4 MB · 7 kept" });
    expect(backupText(backup, "America/New_York", backup.at_ms + 5 * 3_600_000).value).toBe("03:30"); // the same day: the time alone
    expect(backupText(backup, "America/New_York", backup.at_ms + 2 * 60_000).value).toBe("2 min ago");
  });

  it("says there is none, and how to get one", () => {
    expect(backupText(null, "UTC", 0).value).toBe("none yet");
    expect(backupText(undefined, "UTC", 0).note).toContain("--backup-at");
  });

  it("is good while it is fresh, a warning once a night has been missed, and none without one", () => {
    expect(backupLevel(backup, backup.at_ms + 60_000)).toBe("good");
    expect(backupLevel(backup, backup.at_ms + BACKUP_STALE_MS)).toBe("good");
    expect(backupLevel(backup, backup.at_ms + BACKUP_STALE_MS + 1)).toBe("warning");
    expect(backupLevel(null, 0)).toBe("none");
  });
});

describe("the memory test", () => {
  const at = Date.UTC(2026, 9, 9, 19, 22);
  const now = Date.UTC(2026, 9, 10, 12, 0);

  it("reports a pass", () => {
    expect(memoryTestText({ at_ms: at, mb: 512, ok: true }, "America/New_York", now)).toEqual({
      value: "passed",
      note: "512 MB tested once, Fri, Oct 9, 15:22",
      ok: true,
    });
  });

  it("reports a failure as a reason not to trust the board", () => {
    const failed = memoryTestText({ at_ms: at, mb: 64, ok: false }, "UTC", now);
    expect(failed.ok).toBe(false);
    expect(failed.value).toBe("problem found");
    expect(failed.note).toContain("cannot be trusted");
  });

  it("says when it has not been run", () => {
    expect(memoryTestText(null, "UTC", now)).toMatchObject({ value: "not run", ok: null });
  });
});
