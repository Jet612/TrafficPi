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
  it("is live and says how fast", () => {
    expect(deriveStatus(snap({}))).toEqual({ level: "good", label: "Live · 30 fps" });
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
