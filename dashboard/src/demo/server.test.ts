import { describe, expect, it, vi } from "vitest";
import { profileOf, lineOf, stopOf, zoneOf } from "../lib/fixtures";
import type { StopOut, TrailPaths } from "../lib/types";
import { frameAt, liveMessage } from "./playback";
import { requestKey, type LiveRecording, type Recording } from "./recording";
import { DemoServer, NOT_IN_DEMO } from "./server";

function stop(id: number, t: number, over: Partial<StopOut> = {}): StopOut {
  return {
    id,
    t,
    sign: "elm",
    label: "car",
    outcome: "stopped",
    review: null,
    reviewed_at: null,
    hold_s: 1,
    observed_s: 2,
    min_speed: 0,
    min_speed_ms: null,
    max_speed_ms: null,
    line_speed_ms: null,
    calibrated: false,
    snapshot_url: null,
    clip_url: null,
    ...over,
  };
}

const day: TrailPaths = {
  from_ms: 0,
  to_ms: 1000,
  scale: 10_000,
  total: 8,
  returned: 4,
  trails: [100, 200, 600, 700].map((started_at, id) => ({ id, label: "car", started_at, ended_at: started_at + 50, x: [0], y: [0] })),
};

function recording(over: Partial<Recording> = {}): Recording {
  return {
    format: 1,
    recorded_at: 0,
    get: {
      "/api/profile": { profile: profileOf(), version: 3, problem: null, reference_at: null },
      "/api/stats/stops?days=7": { which: "week" },
      "/api/stats/stops?days=1": { which: "day" },
      "/api/settings": { evidence: true },
    },
    stops: [stop(1, 10), stop(2, 20, { outcome: "rolling", sign: "oak" }), stop(3, 30, { review: "false_positive" }), stop(4, 40)],
    trail_days: [{ day: "2026-11-09", from_ms: 0, to_ms: 1000, url: "/demo/trails/2026-11-09.json" }],
    ...over,
  };
}

const at = (path: string) => new URL(path, "http://demo.test");

describe("requestKey", () => {
  it("sorts the parameters, so the order they were written in does not matter", () => {
    expect(requestKey("/api/x", "b=2&a=1")).toBe("/api/x?a=1&b=2");
    expect(requestKey("/api/x", "")).toBe("/api/x");
    expect(requestKey("/api/stats/stops", "days=0&sign=elm%20street%2F1")).toBe("/api/stats/stops?days=0&sign=elm+street%2F1");
  });
});

describe("DemoServer", () => {
  it("answers a recorded GET exactly, and the same path with other parameters when that was not recorded", async () => {
    const server = new DemoServer(recording(), vi.fn());
    expect(await server.handle("GET", at("/api/stats/stops?days=1"), undefined)).toEqual({ status: 200, body: { which: "day" } });
    expect((await server.handle("GET", at("/api/stats/stops?days=30"), undefined)).status).toBe(200);
    expect((await server.handle("GET", at("/api/nothing"), undefined)).status).toBe(404);
  });

  it("lists stops by range, outcome, sign, review and page, newest first", async () => {
    const server = new DemoServer(recording(), vi.fn());
    const ids = async (query: string) =>
      ((await server.handle("GET", at(`/api/stops?${query}`), undefined)).body as StopOut[]).map((one) => one.id);
    expect(await ids("from_ms=0&to_ms=100")).toEqual([4, 3, 2, 1]);
    expect(await ids("from_ms=15&to_ms=40")).toEqual([3, 2]);
    expect(await ids("from_ms=0&to_ms=100&outcome=rolling")).toEqual([2]);
    expect(await ids("from_ms=0&to_ms=100&sign=elm")).toEqual([4, 3, 1]);
    expect(await ids("from_ms=0&to_ms=100&review=counted")).toEqual([4, 2, 1]);
    expect(await ids("from_ms=0&to_ms=100&review=false_positive")).toEqual([3]);
    expect(await ids("from_ms=0&to_ms=100&limit=2&before_id=4")).toEqual([3, 2]);
  });

  it("keeps a review until the page is reloaded, and the summary counts it", async () => {
    const server = new DemoServer(recording(), vi.fn(), () => 99);
    const reply = await server.handle("PUT", at("/api/stops/1/review"), { state: "confirmed" });
    expect(reply.body).toMatchObject({ id: 1, review: "confirmed", reviewed_at: 99 });
    const summary = (await server.handle("GET", at("/api/stops/summary?from_ms=0&to_ms=100"), undefined)).body as {
      outcomes: { outcome: string; total: number; confirmed: number; false_positive: number; unreviewed: number }[];
    };
    expect(summary.outcomes.find((one) => one.outcome === "stopped")).toEqual({
      outcome: "stopped",
      total: 3,
      confirmed: 1,
      false_positive: 1,
      unreviewed: 1,
    });
    expect((await server.handle("PUT", at("/api/stops/77/review"), { state: null })).status).toBe(404);
    expect((await server.handle("PUT", at("/api/stops/1/review"), { state: "maybe" })).status).toBe(422);
  });

  it("saves a profile in memory and draws it on the live view", async () => {
    const server = new DemoServer(recording(), vi.fn());
    expect(server.liveOverlay()).toBeNull();
    const profile = profileOf({ lines: [lineOf("l1", { name: "Main" })], detection: [zoneOf("d1")], stops: [stopOf("s1")] });
    expect((await server.handle("PUT", at("/api/profile"), profile)).body).toEqual({ version: 4, vision_notified: true });
    expect(((await server.handle("GET", at("/api/profile"), undefined)).body as { profile: unknown }).profile).toEqual(profile);
    const overlay = server.liveOverlay();
    expect(overlay?.profile_version).toBe(4);
    expect(overlay?.lines).toEqual([{ id: "l1", name: "Main", a: [0.2, 0.5], b: [0.8, 0.5] }]);
    expect(overlay?.zones.map((zone) => zone.kind)).toEqual(["detection"]);
    expect(overlay?.stop_signs[0]).toMatchObject({ id: "s1", direction: 1 });
  });

  it("refuses what needs the Pi, and signs out and in", async () => {
    const server = new DemoServer(recording(), vi.fn());
    expect(await server.handle("POST", at("/api/profile/reference"), undefined)).toEqual({ status: 501, body: { detail: NOT_IN_DEMO } });
    expect((await server.handle("GET", at("/api/auth/me"), undefined)).status).toBe(200);
    await server.handle("POST", at("/api/auth/logout"), undefined);
    expect((await server.handle("GET", at("/api/auth/me"), undefined)).status).toBe(401);
    expect((await server.handle("GET", at("/api/settings"), undefined)).status).toBe(401);
    await server.handle("POST", at("/api/auth/login"), { username: "anyone", password: "anything" });
    expect((await server.handle("GET", at("/api/settings"), undefined)).body).toEqual({ evidence: true });
  });

  it("gives the trails that began in a range from the day's file, with the total scaled from the sample", async () => {
    const load = vi.fn().mockResolvedValue(day);
    const server = new DemoServer(recording(), load);
    const reply = (await server.handle("GET", at("/api/trails?from_ms=150&to_ms=650"), undefined)).body as TrailPaths;
    expect(reply.trails.map((trail) => trail.started_at)).toEqual([200, 600]);
    expect([reply.returned, reply.total]).toEqual([2, 4]);
    await server.handle("GET", at("/api/trails?from_ms=0&to_ms=1000"), undefined);
    expect(load).toHaveBeenCalledTimes(1);
    expect(load).toHaveBeenCalledWith("/demo/trails/2026-11-09.json");
  });
});

describe("the live loop", () => {
  const live = {
    duration_s: 10,
    base: { lines: [], zones: [], stop_signs: [], profile_version: 1 },
    frames: [0, 0.1, 0.2, 5].map((t, frame) => ({ t, frame, uptime_s: 100 + t, fps: 30, tracks: [], events: [{ t: 1000, line: "l", direction: 1, class: "car", track: 1 }] })),
  } as unknown as LiveRecording;

  it("finds the frame showing at a moment of the loop", () => {
    expect([frameAt(live.frames, 0), frameAt(live.frames, 0.15), frameAt(live.frames, 4.9), frameAt(live.frames, 9)]).toEqual([0, 1, 2, 3]);
  });

  it("moves the times of a replayed loop on, so its crossings are new ones", () => {
    const message = liveMessage(live, 1, 2, 5555, null);
    expect(message).toMatchObject({ type: "state", frame: 1, updated_ms: 5555, uptime_s: 120.1 });
    expect(message.events[0]?.t).toBe(21_000);
    expect("t" in message).toBe(false);
  });
});
