import type {
  AuthState,
  EventOut,
  Profile,
  ProfileCheck,
  ProfileOut,
  ProfileSaved,
  ReferenceSaved,
  SeriesPoint,
  Settings,
  StatusResponse,
  TodaySummary,
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

export const api = {
  authState: () => request<AuthState>("GET", "/api/auth/state"),
  me: () => request<{ username: string }>("GET", "/api/auth/me"),
  login: (username: string, password: string) =>
    request<void>("POST", "/api/auth/login", { username, password }),
  logout: () => request<void>("POST", "/api/auth/logout"),
  status: (signal?: AbortSignal) => request<StatusResponse>("GET", "/api/status", undefined, signal),
  today: (signal?: AbortSignal) => request<TodaySummary>("GET", "/api/stats/today", undefined, signal),
  series: (fromMs: number, toMs: number, bucket: Bucket, signal?: AbortSignal) =>
    request<SeriesPoint[]>(
      "GET",
      `/api/stats/series?from_ms=${fromMs}&to_ms=${toMs}&bucket=${bucket}`,
      undefined,
      signal,
    ),
  events: (limit: number, signal?: AbortSignal) =>
    request<EventOut[]>("GET", `/api/events?limit=${limit}`, undefined, signal),
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
