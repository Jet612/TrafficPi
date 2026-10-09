import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, api, errorMessage, onUnauthorized, referencePictureUrl, snapshotUrl } from "./api";
import { emptyProfile } from "./profile";

function respond(status: number, body?: unknown, headers: Record<string, string> = {}): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), { status, headers });
}

afterEach(() => vi.unstubAllGlobals());

describe("api", () => {
  it("returns parsed JSON and sends cookies, without caching", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { username: "admin" }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await api.me()).toEqual({ username: "admin" });
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(path).toBe("/api/auth/me");
    expect(init).toMatchObject({ method: "GET", credentials: "same-origin", cache: "no-store" });
  });

  it("sends a JSON body and treats 204 as no content", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(204));
    vi.stubGlobal("fetch", fetchMock);
    await expect(api.login("admin", "pw")).resolves.toBeUndefined();
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(init.body).toBe('{"username":"admin","password":"pw"}');
    expect(init.headers).toMatchObject({ "Content-Type": "application/json" });
  });

  it("builds the series query", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, []));
    vi.stubGlobal("fetch", fetchMock);
    await api.series(1000, 2000, "hour");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/stats/series?from_ms=1000&to_ms=2000&bucket=hour");
  });

  it("turns failures into ApiError with the server's words", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(422, { detail: 'lines[0] must be [x, y]' })));
    await expect(api.saveProfile(emptyProfile())).rejects.toMatchObject({
      status: 422,
      message: "lines[0] must be [x, y]",
    });
  });

  it("saves a profile as JSON and checks an imported document without saving it", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(respond(200, { version: 4, vision_notified: true }))
      .mockResolvedValueOnce(respond(200, { ok: false, problem: "lines[0]: is missing", profile: null }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await api.saveProfile(emptyProfile())).toEqual({ version: 4, vision_notified: true });
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect([path, init.method, init.body]).toEqual(["/api/profile", "PUT", JSON.stringify(emptyProfile())]);

    const checked = await api.checkProfile({ lines: "nope" });
    expect(checked).toMatchObject({ ok: false, problem: "lines[0]: is missing" });
    const [checkPath, checkInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect([checkPath, checkInit.method, checkInit.body]).toEqual(["/api/profile/check", "POST", '{"lines":"nope"}']);
  });

  it("captures the reference picture with a POST and no body", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { reference_at: 1_700_000_000_000 }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await api.captureReference()).toEqual({ reference_at: 1_700_000_000_000 });
    const [path, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect([path, init.method, init.body]).toEqual(["/api/profile/reference", "POST", undefined]);
  });

  it("reads the settings and saves all of them at once", async () => {
    const settings = {
      timezone: "UTC",
      units: "mph",
      trails_enabled: true,
      trails_people: false,
      trails_keep_days: 365,
      evidence_enabled: true,
      evidence_keep_days: 30,
      evidence_max_mb: 2048,
      evidence_rolling_clip_hours: 3,
      evidence_did_not_stop_clip_hours: 48,
      evidence_keep_rolling: true,
      evidence_keep_did_not_stop: true,
      remote_live_video: false,
      detect_people: true,
    };
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(respond(200, settings)));
    vi.stubGlobal("fetch", fetchMock);
    expect(await api.settings()).toEqual(settings);
    expect(await api.saveSettings(settings as never)).toEqual(settings);
    const [path, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect([path, init.method, init.body]).toEqual(["/api/settings", "PUT", JSON.stringify(settings)]);
  });

  it("lists the latest counts without the stops, and reviews a stop with a PUT", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(respond(200, [])));
    vi.stubGlobal("fetch", fetchMock);
    await api.events(10);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/events?limit=10&type=count");
    fetchMock.mockImplementation(() => Promise.resolve(respond(200, { id: 5, review: "confirmed" })));
    await api.reviewStop(5, "confirmed");
    const [path, init] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect([path, init.method, init.body]).toEqual(["/api/stops/5/review", "PUT", '{"state":"confirmed"}']);
    await api.reviewStop(5, null);
    expect((fetchMock.mock.calls[2] as [string, RequestInit])[1].body).toBe('{"state":null}');
    await api.stopSummary(1, 2);
    expect(fetchMock.mock.calls[3]?.[0]).toBe("/api/stops/summary?from_ms=1&to_ms=2");
    await api.stops({ fromMs: 1, toMs: 2, limit: 5 });
    expect(fetchMock.mock.calls[4]?.[0]).toBe("/api/stops?from_ms=1&to_ms=2&limit=5");
  });

  it("asks for the data of some local days, at one stop sign if one is named", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(respond(200, {})));
    vi.stubGlobal("fetch", fetchMock);
    await api.stopStats(7);
    await api.stopStats(0, "elm street/1");
    await api.trafficStats(30);
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual([
      "/api/stats/stops?days=7",
      "/api/stats/stops?days=0&sign=elm%20street%2F1",
      "/api/stats/traffic?days=30",
    ]);
  });

  it("asks for a day by the hour, today if none is named", async () => {
    const fetchMock = vi.fn().mockImplementation(() => Promise.resolve(respond(200, { day: "2026-10-09", hours: [] })));
    vi.stubGlobal("fetch", fetchMock);
    await api.trailsDay();
    await api.trailsDay("2026-10-08");
    expect(fetchMock.mock.calls.map((call) => call[0])).toEqual(["/api/trails/day", "/api/trails/day?day=2026-10-08"]);
  });

  it("asks for the paths in a range of time", async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { trails: [], scale: 65535 }));
    vi.stubGlobal("fetch", fetchMock);
    expect((await api.trails(1000, 2000)).scale).toBe(65535);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/trails?from_ms=1000&to_ms=2000");
  });

  it("names the pictures so that a new one is fetched when it changes", () => {
    expect(referencePictureUrl(123)).toBe("/api/profile/reference.jpg?v=123");
    expect(snapshotUrl(7)).toBe("/api/snapshot.jpg?t=7");
  });

  it("reads Retry-After from a throttled login", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(respond(429, { detail: "too many failed logins" }, { "Retry-After": "120" })),
    );
    await expect(api.login("a", "b")).rejects.toMatchObject({ status: 429, retryAfterS: 120 });
  });

  it("says status 0 when the server cannot be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await api.status().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ status: 0, message: "Cannot reach the server" });
  });

  it("announces an ended session on data requests, but not on the login endpoints", async () => {
    const listener = vi.fn();
    const off = onUnauthorized(listener);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(401, { detail: "not logged in" })));
    await expect(api.status()).rejects.toMatchObject({ status: 401 });
    expect(listener).toHaveBeenCalledTimes(1);
    await expect(api.me()).rejects.toMatchObject({ status: 401 }); // the gate asks this itself
    await expect(api.login("a", "b")).rejects.toMatchObject({ status: 401 }); // a wrong password
    expect(listener).toHaveBeenCalledTimes(1);
    off();
    await expect(api.status()).rejects.toMatchObject({ status: 401 });
    expect(listener).toHaveBeenCalledTimes(1);
  });

  it("does not turn an aborted request into a server error", async () => {
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new DOMException("aborted", "AbortError")));
    await expect(api.status(controller.signal)).rejects.toMatchObject({ name: "AbortError" });
  });
});

describe("errorMessage", () => {
  it("joins validation messages", async () => {
    const response = respond(422, { detail: [{ loc: ["body"], msg: "Field required" }, { msg: "bad value" }] });
    expect(await errorMessage(response)).toBe("Field required; bad value");
  });

  it("falls back to the status line", async () => {
    expect(await errorMessage(new Response("<html>", { status: 502, statusText: "Bad Gateway" }))).toBe("502 Bad Gateway");
  });
});
