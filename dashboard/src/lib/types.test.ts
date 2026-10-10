import { describe, expect, it } from "vitest";
import { parseLiveMessage } from "./types";

const state = {
  type: "state",
  uptime_s: 12.5,
  updated_ms: 1_700_000_000_000,
  frame: 99,
  fps: 29.9,
  camera: { ok: true, error: null },
  detector: "yolov8s_h8l",
  profile_version: 3,
  lines: [{ id: "main", name: "Main", a: [0.2, 0.5], b: [0.8, 0.5] }],
  zones: [
    { id: "road", name: "The road", kind: "detection", points: [[0.1, 0.4], [0.9, 0.4], [0.9, 0.9], [0.1, 0.9]] },
    { id: "lot", name: "Parking", kind: "exclusion", points: [[0.1, 0.5], [0.3, 0.5], [0.3, 0.8]] },
  ],
  stop_signs: [
    { id: "elm", name: "Elm Street", approach: [[0.1, 0.5], [0.5, 0.5], [0.5, 0.9], [0.1, 0.9]], a: [0.5, 0.9], b: [0.5, 0.5], direction: -1 },
  ],
  tracks: [
    { id: 4, label: "car", score: 0.91, box: [0.1, 0.2, 0.3, 0.4], velocity: [0, 0.01], confirmed: true },
  ],
  events: [{ t: 1_700_000_000_500, line: "main", direction: 1, class: "car", track: 4 }],
  system: { cpu_cores: 0.2, cpu_percent: 8, rss_mb: 460, soc_temp_c: 66.7, throttled_now: [], throttled_since_boot: ["throttling occurred"] },
  drift: { state: "ok", message: "The camera sees what it saw when the reference picture was kept.", shift_pct: 0.4, checked_at: 1_700_000_000_000 },
};

describe("parseLiveMessage", () => {
  it("reads a whole state message", () => {
    const message = parseLiveMessage(JSON.stringify(state));
    expect(message).toMatchObject({ type: "state", frame: 99, fps: 29.9, detector: "yolov8s_h8l" });
    if (message?.type !== "state") throw new Error("expected a state");
    expect(message.tracks[0]).toMatchObject({ id: 4, label: "car", confirmed: true });
    expect(message.lines[0]?.a).toEqual([0.2, 0.5]);
    expect(message.zones.map((z) => [z.id, z.kind, z.points.length])).toEqual([["road", "detection", 4], ["lot", "exclusion", 3]]);
    expect(message.events[0]).toMatchObject({ direction: 1, class: "car" });
    expect(message.system.throttled_since_boot).toEqual(["throttling occurred"]);
  });

  it("reads a vehicle's speed when the camera is calibrated, and none when it is not or the speed is odd", () => {
    const withSpeeds = {
      ...state,
      tracks: [
        { ...state.tracks[0]!, speed_ms: 11.2 },
        { ...state.tracks[0]!, id: 5 },
        { ...state.tracks[0]!, id: 6, speed_ms: -1 },
        { ...state.tracks[0]!, id: 7, speed_ms: "fast" },
      ],
    };
    const message = parseLiveMessage(JSON.stringify(withSpeeds));
    if (message?.type !== "state") throw new Error("expected a state");
    expect(message.tracks.map((t) => t.speed_ms)).toEqual([11.2, undefined, undefined, undefined]);
  });

  it("reads the drift check's finding", () => {
    const message = parseLiveMessage(JSON.stringify(state));
    if (message?.type !== "state") throw new Error("expected a state");
    expect(message.drift).toMatchObject({ state: "ok", shift_pct: 0.4, checked_at: 1_700_000_000_000, dx_pct: null });
  });

  it("has no drift finding from a service that keeps no check, or sends something odd", () => {
    for (const drift of [{}, null, "ok", { state: "fine" }, undefined]) {
      const message = parseLiveMessage(JSON.stringify({ ...state, drift }));
      if (message?.type !== "state") throw new Error("expected a state");
      expect(message.drift).toBeNull();
    }
  });

  it("reads the stop signs, and drops one that is half there", () => {
    const message = parseLiveMessage(JSON.stringify(state));
    if (message?.type !== "state") throw new Error("expected a state");
    expect(message.stop_signs).toEqual([
      { id: "elm", name: "Elm Street", approach: [[0.1, 0.5], [0.5, 0.5], [0.5, 0.9], [0.1, 0.9]], a: [0.5, 0.9], b: [0.5, 0.5], direction: -1 },
    ]);
    const damaged = {
      ...state,
      stop_signs: [
        state.stop_signs[0],
        { id: "no-line", approach: [[0, 0], [1, 0], [1, 1]], a: [0.5], b: [0.5, 0.5] },
        { id: "no-zone", approach: [[0, 0], [1, 0]], a: [0.5, 0.9], b: [0.5, 0.5] },
        { id: "bad-corner", approach: [[0, 0], [1, 0], ["x", 1]], a: [0.5, 0.9], b: [0.5, 0.5] },
        { id: "plain", approach: [[0, 0], [1, 0], [1, 1]], a: [0.5, 0.9], b: [0.5, 0.5] },
        7,
      ],
    };
    const again = parseLiveMessage(JSON.stringify(damaged));
    if (again?.type !== "state") throw new Error("expected a state");
    expect(again.stop_signs.map((s) => s.id)).toEqual(["elm", "plain"]);
    expect(again.stop_signs[1]).toMatchObject({ name: "", direction: 1 }); // what is missing is the usual
  });

  it("has no stop signs when an older vision service does not send any", () => {
    const { stop_signs: _unused, ...older } = state;
    void _unused;
    const message = parseLiveMessage(JSON.stringify(older));
    if (message?.type !== "state") throw new Error("expected a state");
    expect(message.stop_signs).toEqual([]);
  });

  it("reads the offline notice", () => {
    expect(parseLiveMessage('{"type":"offline"}')).toEqual({ type: "offline" });
  });

  it("refuses what is not a message", () => {
    for (const text of ["", "not json", "[]", "null", "42", '{"type":"something-else"}', "{}"]) {
      expect(parseLiveMessage(text)).toBeNull();
    }
  });

  it("drops bad list entries but keeps the rest", () => {
    const messy = {
      ...state,
      tracks: [state.tracks[0], { id: "x" }, { id: 5, label: "car", box: [1, 2, 3] }, null, 7],
      lines: [{ id: "ok", a: [0, 0], b: [1, 1] }, { id: "bad", a: [0], b: [1, 1] }],
      events: [{ t: "now", line: "main" }, state.events[0]],
    };
    const message = parseLiveMessage(JSON.stringify(messy));
    if (message?.type !== "state") throw new Error("expected a state");
    expect(message.tracks.map((t) => t.id)).toEqual([4]);
    expect(message.lines.map((l) => l.id)).toEqual(["ok"]);
    expect(message.lines[0]?.name).toBe("");
    expect(message.events).toHaveLength(1);
  });

  it("fills in sane defaults for missing fields", () => {
    const message = parseLiveMessage('{"type":"state"}');
    expect(message).toMatchObject({
      type: "state",
      frame: -1,
      fps: 0,
      camera: { ok: false, error: null },
      tracks: [],
      lines: [],
      zones: [],
      events: [],
      system: { cpu_cores: null, throttled_now: [] },
      drift: null,
    });
  });

  it("drops a zone that is not a usable outline, and keeps the others", () => {
    const [road] = state.zones;
    const messy = {
      ...state,
      zones: [
        road,
        { id: "a", kind: "nonsense", points: road!.points },
        { id: "b", kind: "detection", points: [[0.1, 0.1], [0.2, 0.2]] }, // two corners
        { id: "c", kind: "detection", points: [[0.1, 0.1], [0.2], [0.3, 0.3]] }, // one is not a pair
        { kind: "detection", points: road!.points }, // no id
        null,
      ],
    };
    const message = parseLiveMessage(JSON.stringify(messy));
    if (message?.type !== "state") throw new Error("expected a state");
    expect(message.zones.map((z) => z.id)).toEqual(["road"]);
  });

  it("reads a zone's name as empty when it has none", () => {
    const message = parseLiveMessage(
      JSON.stringify({ ...state, zones: [{ id: "z", kind: "exclusion", points: [[0.1, 0.1], [0.5, 0.1], [0.5, 0.5]] }] }),
    );
    if (message?.type !== "state") throw new Error("expected a state");
    expect(message.zones[0]).toMatchObject({ id: "z", name: "", kind: "exclusion" });
  });

  it("only believes an unconfirmed track when it says so", () => {
    const message = parseLiveMessage(
      JSON.stringify({ ...state, tracks: [{ ...state.tracks[0], confirmed: "yes" }] }),
    );
    if (message?.type !== "state") throw new Error("expected a state");
    expect(message.tracks[0]?.confirmed).toBe(false);
  });
});
