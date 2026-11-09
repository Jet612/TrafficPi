/**
 * The API as the static demo answers it, in the browser: plain GETs from the recording, the lists
 * that are asked for by time range filtered from it, and the few changes a visitor can make (the
 * profile, the settings, the stop-sign hours, reviewing a pass, signing out and in) kept in memory
 * until the page is reloaded. Everything else that would change the Pi is refused, with a reason.
 */
import type {
  LiveState,
  Profile,
  ProfileOut,
  ReviewState,
  StopHoursConfig,
  StopHoursState,
  StopOut,
  StopSummary,
  TrailPaths,
} from "@/lib/types";
import { requestKey, type Recording, type TrailDayPaths } from "./recording";

export interface DemoReply {
  status: number;
  /** Sent as JSON; none for 204. */
  body?: unknown;
}

/** The part of the live state that comes from the profile, once a visitor has saved one. */
export type LiveOverlay = Pick<LiveState, "lines" | "zones" | "stop_signs" | "profile_version">;

export const NOT_IN_DEMO = "The demo cannot do this: it needs a real TrafficPi.";

const OUTCOMES = ["stopped", "rolling", "did_not_stop", "indeterminate"] as const;
const REVIEW_STATES: readonly (ReviewState | null)[] = ["confirmed", "false_positive", null];
const STOP_PAGE_LIMIT = 50;

const ok = (body: unknown): DemoReply => ({ status: 200, body });
const fail = (status: number, detail: string): DemoReply => ({ status, body: { detail } });

/** The lines, zones and stop signs the live view draws, from a profile. */
export function overlayOf(profile: Profile, version: number): LiveOverlay {
  return {
    profile_version: version,
    lines: profile.lines.map(({ id, name, a, b }) => ({ id, name, a, b })),
    zones: [
      ...profile.detection_zones.map(({ id, name, points }) => ({ id, name, kind: "detection" as const, points })),
      ...profile.exclusion_zones.map(({ id, name, points }) => ({ id, name, kind: "exclusion" as const, points })),
    ],
    stop_signs: profile.stop_signs.map(({ id, name, approach, a, b, direction }) => ({ id, name, approach, a, b, direction: direction < 0 ? -1 : 1 })),
  };
}

export class DemoServer {
  private signedIn = true;
  private readonly stops: StopOut[];
  private profile: ProfileOut | null;
  private settings: unknown;
  private stopHours: StopHoursState | null;
  private overlay: LiveOverlay | null = null;
  private readonly trailFiles = new Map<string, Promise<TrailDayPaths>>();

  constructor(
    private readonly recording: Recording,
    /** Fetches one of the recording's other files. */
    private readonly loadJson: (url: string) => Promise<unknown>,
    private readonly now: () => number = () => Date.now(),
  ) {
    this.stops = recording.stops.map((stop) => ({ ...stop })).sort((a, b) => b.id - a.id);
    this.profile = (recording.get["/api/profile"] as ProfileOut | undefined) ?? null;
    this.settings = recording.get["/api/settings"];
    this.stopHours = (recording.get["/api/stops/hours"] as StopHoursState | undefined) ?? null;
  }

  /** What the live view draws from the profile, if a visitor has changed it; null while it is the recorded one. */
  liveOverlay(): LiveOverlay | null {
    return this.overlay;
  }

  async handle(method: string, url: URL, body: unknown): Promise<DemoReply> {
    const path = url.pathname.replace(/\/+$/, "");
    if (method === "GET") return this.get(path, url.searchParams);
    return this.change(method, path, body);
  }

  private async get(path: string, params: URLSearchParams): Promise<DemoReply> {
    switch (path) {
      case "/api/auth/state":
        return ok({ configured: true });
      case "/api/auth/me":
        return this.signedIn ? ok({ username: "visitor", remote: false, live_video: true }) : fail(401, "Not signed in");
    }
    if (!this.signedIn) return fail(401, "Not signed in");
    switch (path) {
      case "/api/stops":
        return ok(this.listStops(params));
      case "/api/stops/summary":
        return ok(this.summary(Number(params.get("from_ms")), Number(params.get("to_ms"))));
      case "/api/trails":
        return ok(await this.trails(Number(params.get("from_ms")), Number(params.get("to_ms"))));
      case "/api/profile":
        return this.profile ? ok(this.profile) : fail(404, "No profile in this recording");
      case "/api/settings":
        if (this.settings !== undefined) return ok(this.settings);
        break;
      case "/api/stops/hours":
        if (this.stopHours) return ok(this.stopHours);
        break;
    }
    const recorded = this.recording.get;
    const exact = recorded[requestKey(path, params)];
    if (exact !== undefined) return ok(exact);
    // A question the recording did not ask (another number of days, say): the nearest it has is
    // the same path with other parameters.
    const similar = Object.keys(recorded).find((key) => key === path || key.startsWith(`${path}?`));
    if (similar !== undefined) return ok(recorded[similar]);
    return fail(404, "Not in this recording");
  }

  private change(method: string, path: string, body: unknown): DemoReply {
    if (method === "POST" && path === "/api/auth/login") {
      this.signedIn = true;
      return { status: 204 };
    }
    if (method === "POST" && path === "/api/auth/logout") {
      this.signedIn = false;
      return { status: 204 };
    }
    if (!this.signedIn) return fail(401, "Not signed in");
    if (method === "PUT" && path === "/api/profile" && this.profile) {
      const profile = body as Profile;
      const version = this.profile.version + 1;
      this.profile = { ...this.profile, profile, version, problem: null };
      this.overlay = overlayOf(profile, version);
      return ok({ version, vision_notified: true });
    }
    if (method === "PUT" && path === "/api/settings" && this.settings !== undefined) {
      this.settings = body;
      return ok(body);
    }
    if (method === "PUT" && path === "/api/stops/hours" && this.stopHours) {
      this.stopHours = { ...this.stopHours, config: body as StopHoursConfig };
      return ok({ ...this.stopHours, vision_notified: true });
    }
    const review = /^\/api\/stops\/(\d+)\/review$/.exec(path);
    if (method === "PUT" && review) {
      const state = (body as { state?: unknown } | null)?.state ?? null;
      if (!REVIEW_STATES.includes(state as ReviewState | null)) return fail(422, "state must be confirmed, false_positive or null");
      const stop = this.stops.find((one) => one.id === Number(review[1]));
      if (!stop) return fail(404, "no such stop event");
      stop.review = state as ReviewState | null;
      stop.reviewed_at = state === null ? null : this.now();
      return ok({ ...stop });
    }
    return fail(501, NOT_IN_DEMO);
  }

  /** `/api/stops`: in [from_ms, to_ms), newest first, by outcome, review and sign, a page at a time. */
  private listStops(params: URLSearchParams): StopOut[] {
    const from = Number(params.get("from_ms"));
    const to = Number(params.get("to_ms"));
    const outcomes = params.get("outcome")?.split(",");
    const review = params.get("review");
    const sign = params.get("sign");
    const before = params.has("before_id") ? Number(params.get("before_id")) : Infinity;
    const limit = Number(params.get("limit") ?? STOP_PAGE_LIMIT) || STOP_PAGE_LIMIT;
    return this.stops
      .filter(
        (stop) =>
          stop.t >= from &&
          stop.t < to &&
          stop.id < before &&
          (outcomes === undefined || outcomes.includes(stop.outcome)) &&
          (sign === null || stop.sign === sign) &&
          (review === null ||
            (review === "unreviewed" && stop.review === null) ||
            (review === "counted" && stop.review !== "false_positive") ||
            stop.review === review),
      )
      .slice(0, limit)
      .map((stop) => ({ ...stop }));
  }

  /** `/api/stops/summary`: how many passes had each outcome in the range, and how many were looked at. */
  private summary(from: number, to: number): StopSummary {
    const inRange = this.stops.filter((stop) => stop.t >= from && stop.t < to);
    return {
      from_ms: from,
      to_ms: to,
      outcomes: OUTCOMES.map((outcome) => {
        const these = inRange.filter((stop) => stop.outcome === outcome);
        return {
          outcome,
          total: these.length,
          confirmed: these.filter((stop) => stop.review === "confirmed").length,
          false_positive: these.filter((stop) => stop.review === "false_positive").length,
          unreviewed: these.filter((stop) => stop.review === null).length,
        };
      }),
    };
  }

  /**
   * `/api/trails`: the recorded paths that began in the range. A day's file is an even sample when
   * the day had many trails, so the total is scaled up from the sample in the same proportion.
   */
  private async trails(from: number, to: number): Promise<TrailPaths> {
    const days = this.recording.trail_days.filter((day) => day.from_ms < to && day.to_ms > from);
    const files = await Promise.all(days.map((day) => this.trailFile(day.url)));
    const trails = files.flatMap((file) => file.trails.filter((trail) => trail.started_at >= from && trail.started_at < to));
    const total = files.reduce((sum, file) => {
      const shown = file.trails.filter((trail) => trail.started_at >= from && trail.started_at < to).length;
      return sum + (file.returned > 0 ? Math.round((shown * file.total) / file.returned) : 0);
    }, 0);
    return { from_ms: from, to_ms: to, scale: files[0]?.scale ?? 10_000, total, returned: trails.length, trails };
  }

  private trailFile(url: string): Promise<TrailDayPaths> {
    let file = this.trailFiles.get(url);
    if (!file) {
      file = this.loadJson(url) as Promise<TrailDayPaths>;
      file.catch(() => this.trailFiles.delete(url));
      this.trailFiles.set(url, file);
    }
    return file;
  }
}
