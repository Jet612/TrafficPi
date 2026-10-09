import type {
  AuthState,
  EventOut,
  Me,
  Profile,
  ProfileCheck,
  ProfileOut,
  ProfileSaved,
  ReferenceSaved,
  ReviewState,
  SeriesPoint,
  Settings,
  StatusResponse,
  StopOut,
  StopStats,
  StopSummary,
  SystemHistory,
  TodaySummary,
  TrafficStats,
  TrailDay,
  TrailPaths,
} from "./types";

/** An answer that is not a success. Status 0 means the server could not be reached at all. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly retryAfterS?: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

const unauthorizedListeners = new Set<() => void>();

/** Called when a data request is refused because the session ended. Returns an unsubscribe. */
export function onUnauthorized(listener: () => void): () => void {
  unauthorizedListeners.add(listener);
  return () => void unauthorizedListeners.delete(listener);
}

/** FastAPI errors are {"detail": "text"} or {"detail": [{"loc": [...], "msg": "text"}]}. */
export async function errorMessage(response: Response): Promise<string> {
  try {
    const body: unknown = await response.json();
    const detail = (body as { detail?: unknown } | null)?.detail;
    if (typeof detail === "string") return detail;
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) => (item as { msg?: unknown } | null)?.msg)
        .filter((msg): msg is string => typeof msg === "string");
      if (messages.length > 0) return messages.join("; ");
    }
  } catch {
    // not JSON: fall through to the status line
  }
  return `${response.status} ${response.statusText}`.trim();
}

async function request<T>(
  method: "GET" | "POST" | "PUT",
  path: string,
  body?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      cache: "no-store",
      headers: body === undefined ? { Accept: "application/json" } : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
  } catch (error) {
    if (signal?.aborted) throw error;
    throw new ApiError(0, "Cannot reach the server");
  }
  if (response.status === 401 && !path.startsWith("/api/auth/")) {
    unauthorizedListeners.forEach((listener) => listener());
  }
  if (!response.ok) {
    const retryAfter = Number.parseInt(response.headers.get("Retry-After") ?? "", 10);
    throw new ApiError(response.status, await errorMessage(response), Number.isFinite(retryAfter) ? retryAfter : undefined);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export type Bucket = "minute" | "hour" | "day";

export interface StopsQuery {
  fromMs: number;
  toMs: number;
  /** Only these outcomes (all when missing or when every one is named is the same thing). */
  outcomes?: readonly string[];
  review?: "unreviewed" | "confirmed" | "false_positive";
  sign?: string;
  limit?: number;
  beforeId?: number;
}

/** The query string of a list of stops: only what was asked for is in it. */
export function stopsQuery(query: StopsQuery): string {
  const params = new URLSearchParams({ from_ms: String(query.fromMs), to_ms: String(query.toMs) });
  if (query.outcomes !== undefined) params.set("outcome", query.outcomes.join(","));
  if (query.review !== undefined) params.set("review", query.review);
  if (query.sign !== undefined) params.set("sign", query.sign);
  if (query.limit !== undefined) params.set("limit", String(query.limit));
  if (query.beforeId !== undefined) params.set("before_id", String(query.beforeId));
  return params.toString();
}

export const api = {
  authState: () => request<AuthState>("GET", "/api/auth/state"),
  me: () => request<Me>("GET", "/api/auth/me"),
  login: (username: string, password: string) =>
    request<void>("POST", "/api/auth/login", { username, password }),
  logout: () => request<void>("POST", "/api/auth/logout"),
  status: (signal?: AbortSignal) => request<StatusResponse>("GET", "/api/status", undefined, signal),
  /** How the machine has been over the last `hours` hours (1 to 720): points to chart, and what is worth telling. */
  systemHistory: (hours: number, signal?: AbortSignal) =>
    request<SystemHistory>("GET", `/api/system/history?hours=${hours}`, undefined, signal),
  today: (signal?: AbortSignal) => request<TodaySummary>("GET", "/api/stats/today", undefined, signal),
  series: (fromMs: number, toMs: number, bucket: Bucket, signal?: AbortSignal) =>
    request<SeriesPoint[]>(
      "GET",
      `/api/stats/series?from_ms=${fromMs}&to_ms=${toMs}&bucket=${bucket}`,
      undefined,
      signal,
    ),
  /** The newest counts (not the stops, which have their own list). */
  events: (limit: number, signal?: AbortSignal) =>
    request<EventOut[]>("GET", `/api/events?limit=${limit}&type=count`, undefined, signal),
  /** What vehicles did at stop signs in [fromMs, toMs), newest first; `beforeId` asks for the page after the one that ended there. */
  stops: (query: StopsQuery, signal?: AbortSignal) => request<StopOut[]>("GET", `/api/stops?${stopsQuery(query)}`, undefined, signal),
  stopSummary: (fromMs: number, toMs: number, signal?: AbortSignal) =>
    request<StopSummary>("GET", `/api/stops/summary?from_ms=${fromMs}&to_ms=${toMs}`, undefined, signal),
  /** How vehicles did at the stop signs over the last `days` local days, today included (0: since the first pass), at one sign if named. */
  stopStats: (days: number, sign?: string, signal?: AbortSignal) =>
    request<StopStats>("GET", `/api/stats/stops?days=${days}${sign === undefined ? "" : `&sign=${encodeURIComponent(sign)}`}`, undefined, signal),
  /** Vehicles counted over the last `days` local days, today included (0: since the first count). */
  trafficStats: (days: number, signal?: AbortSignal) => request<TrafficStats>("GET", `/api/stats/traffic?days=${days}`, undefined, signal),
  /** What a person made of a stop event; null takes it back. */
  reviewStop: (id: number, state: ReviewState | null) => request<StopOut>("PUT", `/api/stops/${id}/review`, { state }),
  profile: (signal?: AbortSignal) => request<ProfileOut>("GET", "/api/profile", undefined, signal),
  saveProfile: (profile: Profile) => request<ProfileSaved>("PUT", "/api/profile", profile),
  /** Whether a document (a file being imported) is a usable profile; nothing is saved. */
  checkProfile: (document: unknown) => request<ProfileCheck>("POST", "/api/profile/check", document),
  /** Keep the camera's current view as the picture the lines and zones are drawn on. */
  captureReference: () => request<ReferenceSaved>("POST", "/api/profile/reference"),
  settings: (signal?: AbortSignal) => request<Settings>("GET", "/api/settings", undefined, signal),
  /** Every member of the settings, which replace what is stored. */
  saveSettings: (settings: Settings) => request<Settings>("PUT", "/api/settings", settings),
  /** A local day ("2026-10-09"; today where the camera is, if none) by the hour: when trails began. */
  trailsDay: (day?: string, signal?: AbortSignal) =>
    request<TrailDay>("GET", day === undefined ? "/api/trails/day" : `/api/trails/day?day=${encodeURIComponent(day)}`, undefined, signal),
  /** The paths of the trails that began in [fromMs, toMs): all of them, or an even sample if there are very many. */
  trails: (fromMs: number, toMs: number, signal?: AbortSignal) =>
    request<TrailPaths>("GET", `/api/trails?from_ms=${fromMs}&to_ms=${toMs}`, undefined, signal),
};

/** The picture the profile was drawn on. `takenAt` changes when it is replaced, so a new one is fetched. */
export const referencePictureUrl = (takenAt: number): string => `/api/profile/reference.jpg?v=${takenAt}`;

/** What the camera sees now. `nonce` makes the browser ask again. */
export const snapshotUrl = (nonce: number): string => `/api/snapshot.jpg?t=${nonce}`;
