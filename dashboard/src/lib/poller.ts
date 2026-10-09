export interface PollerOptions<T> {
  fetcher: (signal: AbortSignal) => Promise<T>;
  intervalMs: number;
  onData: (data: T) => void;
  onError: (error: unknown) => void;
  /** Polling pauses while the page is hidden. */
  isHidden?: () => boolean;
  /** After failures the wait doubles, up to this. */
  maxBackoffMs?: number;
}

const pageHidden = () => typeof document !== "undefined" && document.hidden;

/**
 * Fetches on a schedule: never two requests at once, nothing while the page is hidden, a longer
 * wait after failures, and an immediate fetch when the page comes back or on request.
 */
export class Poller<T> {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private controller: AbortController | null = null;
  private failures = 0;
  private running = false;

  constructor(private readonly options: PollerOptions<T>) {}

  start(): void {
    if (this.running) return;
    this.running = true;
    void this.tick();
  }

  stop(): void {
    this.running = false;
    this.clearTimer();
    this.controller?.abort();
    this.controller = null;
  }

  /** Fetch now (and keep the schedule going from here). */
  refresh(): void {
    if (!this.running) return;
    this.clearTimer();
    void this.tick();
  }

  /** The page was shown or hidden: show means catch up at once. */
  visibilityChanged(): void {
    const hidden = (this.options.isHidden ?? pageHidden)();
    if (!hidden && this.timer !== null) this.refresh();
  }

  private clearTimer(): void {
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  private schedule(): void {
    if (!this.running) return;
    const { intervalMs, maxBackoffMs = 30_000 } = this.options;
    const wait = Math.min(intervalMs * 2 ** this.failures, Math.max(maxBackoffMs, intervalMs));
    this.timer = setTimeout(() => {
      this.timer = null;
      void this.tick();
    }, wait);
  }

  private async tick(): Promise<void> {
    if (!this.running) return;
    if ((this.options.isHidden ?? pageHidden)()) {
      this.schedule();
      return;
    }
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    try {
      const data = await this.options.fetcher(controller.signal);
      if (!this.running || controller.signal.aborted) return;
      this.failures = 0;
      this.options.onData(data);
    } catch (error) {
      if (!this.running || controller.signal.aborted) return;
      this.failures += 1;
      this.options.onError(error);
    }
    if (this.running && this.controller === controller) this.schedule();
  }
}
