/**
 * Stands in for the live WebSocket (`/api/live`) in the demo: it plays the recorded messages in
 * step with the looping video, ten a second, as the vision service would send them.
 */
import type { SocketLike } from "@/lib/live";
import { frameAt, liveMessage, playbackPosition } from "./playback";
import type { LiveRecording } from "./recording";
import type { DemoServer } from "./server";

const TICK_MS = 100;
/** The vision service sends the same frame again after a second, so a quiet link is not taken for a dead one. */
const RESEND_MS = 1000;

export class DemoSocket implements SocketLike {
  onopen: ((event: unknown) => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;
  onerror: ((event: unknown) => void) | null = null;
  readyState = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private closed = false;

  constructor(ready: Promise<{ live: LiveRecording; server: DemoServer }>) {
    ready.then(
      ({ live, server }) => {
        if (this.closed) return;
        this.readyState = 1;
        this.onopen?.({});
        let last = { index: -1, loop: -1, at: 0 };
        const tick = () => {
          const now = Date.now();
          const { loop, t } = playbackPosition(live.duration_s, now);
          const index = frameAt(live.frames, t);
          if (index === last.index && loop === last.loop && now - last.at < RESEND_MS) return;
          last = { index, loop, at: now };
          this.onmessage?.({ data: JSON.stringify(liveMessage(live, index, loop, now, server.liveOverlay())) });
        };
        tick();
        this.timer = setInterval(tick, TICK_MS);
      },
      () => {
        if (this.closed) return;
        this.readyState = 3;
        this.onclose?.({ code: 1011 });
      },
    );
  }

  send(): void {
    // The live socket only listens.
  }

  close(): void {
    this.closed = true;
    this.readyState = 3;
    if (this.timer !== null) clearInterval(this.timer);
    this.timer = null;
  }
}
