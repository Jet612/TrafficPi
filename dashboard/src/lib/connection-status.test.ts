import { describe, expect, it } from "vitest";
import { deriveStatus, overlayIsCurrent } from "./connection-status";
import type { LiveSnapshot } from "./live";
import type { LiveState } from "./types";

const state = (over: Partial<LiveState> = {}): LiveState => ({
  type: "state",
  uptime_s: 1,
  updated_ms: 1,
  frame: 1,
  fps: 29.9,
  camera: { ok: true, error: null },
  detector: "m",
  profile_version: 1,
  lines: [],
  zones: [],
  stop_signs: [],
  tracks: [],
  events: [],
  system: { throttled_now: [], throttled_since_boot: [] },
  drift: null,
  ...over,
});

const snap = (over: Partial<LiveSnapshot>): LiveSnapshot => ({ connection: "open", state: state(), visionOnline: true, ...over });

describe("deriveStatus", () => {
  it("is live and says how fast, apart from the word that is announced", () => {
    expect(deriveStatus(snap({}))).toEqual({ level: "good", label: "Live", extra: "30 fps" });
  });

  it("turns amber while the Pi is slowing itself down, and says why", () => {
    const hot = deriveStatus(snap({ state: state({ system: { throttled_now: ["throttled", "soft temperature limit"], throttled_since_boot: [] } }) }));
    expect(hot).toEqual({
      level: "warning",
      label: "Throttled",
      extra: "30 fps",
      detail: "The CPU is throttled; it is running hot.",
    });
  });

  it("calls a weak power supply that, and not throttling", () => {
    const weak = deriveStatus(snap({ state: state({ system: { throttled_now: ["under-voltage"], throttled_since_boot: [] } }) }));
    expect(weak).toMatchObject({ level: "warning", label: "Weak power", detail: "The power supply is too weak." });
    const both = deriveStatus(snap({ state: state({ system: { throttled_now: ["under-voltage", "throttled"], throttled_since_boot: [] } }) }));
    expect(both).toMatchObject({ level: "warning", label: "Throttled" });
  });

  it("does not hold what happened since the Pi started against it now", () => {
    const earlier = deriveStatus(snap({ state: state({ system: { throttled_now: [], throttled_since_boot: ["throttling occurred"] } }) }));
    expect(earlier).toMatchObject({ level: "good", label: "Live" });
  });

  it("says why the camera is offline", () => {
    const status = deriveStatus(snap({ state: state({ camera: { ok: false, error: "camera busy" } }) }));
    expect(status).toMatchObject({ level: "critical", label: "Camera offline", detail: "camera busy" });
  });

  it("tells a stopped vision service from a broken camera", () => {
    expect(deriveStatus(snap({ visionOnline: false }))).toMatchObject({ level: "critical", label: "Vision service offline" });
  });

  it("tells a lost connection to the Pi from both", () => {
    expect(deriveStatus(snap({ connection: "retrying" }))).toMatchObject({ level: "critical", label: "Connection lost" });
  });

  it("is neutral while it has nothing to report yet", () => {
    expect(deriveStatus(snap({ connection: "connecting", state: null }))).toMatchObject({ level: "neutral" });
    expect(deriveStatus(snap({ state: null }))).toMatchObject({ level: "neutral", label: "Waiting for data…" });
    expect(deriveStatus(snap({ connection: "paused" })).level).toBe("neutral");
    expect(deriveStatus(snap({ connection: "closed" })).level).toBe("neutral");
  });
});

describe("overlayIsCurrent", () => {
  it("is only true when the picture and the boxes are live", () => {
    expect(overlayIsCurrent(snap({}))).toBe(true);
    expect(overlayIsCurrent(snap({ connection: "retrying" }))).toBe(false);
    expect(overlayIsCurrent(snap({ visionOnline: false }))).toBe(false);
    expect(overlayIsCurrent(snap({ state: state({ camera: { ok: false, error: null } }) }))).toBe(false);
    expect(overlayIsCurrent(snap({ state: null }))).toBe(false);
  });
});
