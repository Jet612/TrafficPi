import { parseLiveMessage, type LiveState } from "./types";

export type Connection = "connecting" | "open" | "retrying" | "paused" | "closed";

export interface LiveSnapshot {
  connection: Connection;
  /** The newest state message. Kept while reconnecting, so the picture does not vanish at once. */
  state: LiveState | null;
  /** False while the API says it cannot reach the vision service. */
  visionOnline: boolean;
}

/** The part of a WebSocket that the client uses, so tests can stand in for it. */
export interface SocketLike {
  onopen: ((event: unknown) => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: ((event: unknown) => void) | null;
  close(): void;
}

export interface LiveClientOptions {
  url: () => string;
  createSocket?: (url: string) => SocketLike;
  /** The server shut the socket for policy reasons (code 1008): usually the session has ended. */
  onRefused?: () => void;
  /** A socket that says nothing for this long is dropped and opened again. */
  silenceMs?: number;
  retryDelaysMs?: number[];
}

/** The vision service sends at least one message a second, so five seconds of silence is a dead link. */
const DEFAULT_SILENCE_MS = 5000;
const DEFAULT_RETRY_DELAYS_MS = [1000, 2000, 4000, 5000];
const POLICY_VIOLATION = 1008;

/**
 * The live socket: connects, reconnects with growing pauses, notices a link that went quiet, and
 * can be paused while the page is hidden. Listeners are told after every change; the snapshot is
 * replaced (never mutated), so it works with React's useSyncExternalStore.
 */
export class LiveClient {
  private snapshot: LiveSnapshot = { connection: "connecting", state: null, visionOnline: true };
  private readonly listeners = new Set<() => void>();
  private socket: SocketLike | null = null;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private watchdog: ReturnType<typeof setTimeout> | null = null;
  private attempts = 0;
  private started = false;
  private paused = false;

  constructor(private readonly options: LiveClientOptions) {}

  subscribe = (listener: () => void): (() => void) => {
    this.listeners.add(listener);
    return () => void this.listeners.delete(listener);
  };

  getSnapshot = (): LiveSnapshot => this.snapshot;

  start(): void {
    if (this.started) return;
    this.started = true;
    if (!this.paused) this.connect();
  }

  stop(): void {
    this.started = false;
    this.teardown();
    this.update({ connection: "closed" });
  }

  /** While paused (the page is hidden) there is no socket; resuming reconnects at once. */
  setPaused(paused: boolean): void {
    if (paused === this.paused) return;
    this.paused = paused;
    if (!this.started) return;
    if (paused) {
      this.teardown();
      this.update({ connection: "paused" });
    } else {
      this.attempts = 0;
      this.connect();
    }
  }

  private update(change: Partial<LiveSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...change };
    this.listeners.forEach((listener) => listener());
  }

  private teardown(): void {
    if (this.retryTimer !== null) clearTimeout(this.retryTimer);
    if (this.watchdog !== null) clearTimeout(this.watchdog);
    this.retryTimer = this.watchdog = null;
    const socket = this.socket;
    this.socket = null;
    if (socket) {
      socket.onopen = socket.onmessage = socket.onclose = socket.onerror = null;
      socket.close();
    }
  }

  private connect(): void {
    this.teardown();
    this.update({ connection: this.attempts === 0 ? "connecting" : "retrying" });
    const create = this.options.createSocket ?? ((url: string) => new WebSocket(url) as SocketLike);
    let socket: SocketLike;
    try {
      socket = create(this.options.url());
    } catch {
      this.scheduleRetry();
      return;
    }
    this.socket = socket;
    socket.onopen = () => {
      this.attempts = 0;
      this.update({ connection: "open" });
      this.armWatchdog();
    };
    socket.onmessage = (event) => {
      this.armWatchdog();
      const message = typeof event.data === "string" ? parseLiveMessage(event.data) : null;
      if (message?.type === "state") this.update({ state: message, visionOnline: true });
      else if (message?.type === "offline") this.update({ visionOnline: false });
    };
    socket.onclose = (event) => {
      this.socket = null;
      if (this.watchdog !== null) clearTimeout(this.watchdog);
      this.watchdog = null;
      if (event.code === POLICY_VIOLATION) this.options.onRefused?.();
      this.scheduleRetry();
    };
    socket.onerror = () => {
      // A close event follows; the retry is scheduled there.
    };
  }

  private armWatchdog(): void {
    if (this.watchdog !== null) clearTimeout(this.watchdog);
    this.watchdog = setTimeout(() => {
      this.watchdog = null;
      this.teardown();
      this.scheduleRetry();
    }, this.options.silenceMs ?? DEFAULT_SILENCE_MS);
  }

  private scheduleRetry(): void {
    if (!this.started || this.paused) return;
    const delays = this.options.retryDelaysMs ?? DEFAULT_RETRY_DELAYS_MS;
    const delay = delays[Math.min(this.attempts, delays.length - 1)] ?? 1000;
    this.attempts += 1;
    this.update({ connection: "retrying" });
    this.retryTimer = setTimeout(() => {
      this.retryTimer = null;
      this.connect();
    }, delay);
  }
}
