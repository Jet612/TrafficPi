import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { LiveClient, type SocketLike } from "./live";

class FakeSocket implements SocketLike {
  static instances: FakeSocket[] = [];
  onopen: SocketLike["onopen"] = null;
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;
  closed = false;
  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }
  close(): void {
    this.closed = true;
  }
  open(): void {
    this.onopen?.({});
  }
  send(data: unknown): void {
    this.onmessage?.({ data: typeof data === "string" ? data : JSON.stringify(data) });
  }
  drop(code = 1006): void {
    this.onclose?.({ code });
  }
}

const state = (frame: number) => ({ type: "state", frame, fps: 30, camera: { ok: true, error: null } });
const last = () => FakeSocket.instances[FakeSocket.instances.length - 1]!;

function client(extra: { onRefused?: () => void } = {}) {
  return new LiveClient({
    url: () => "ws://pi/api/live",
    createSocket: (url) => new FakeSocket(url),
    retryDelaysMs: [1000, 2000, 4000, 5000],
    silenceMs: 5000,
    ...extra,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  FakeSocket.instances = [];
});
afterEach(() => vi.useRealTimers());

describe("LiveClient", () => {
  it("connects, then shows what arrives", () => {
    const live = client();
    const seen: string[] = [];
    live.subscribe(() => seen.push(live.getSnapshot().connection));
    live.start();
    expect(live.getSnapshot().connection).toBe("connecting");
    expect(last().url).toBe("ws://pi/api/live");
    last().open();
    last().send(state(7));
    expect(live.getSnapshot()).toMatchObject({ connection: "open", visionOnline: true, state: { frame: 7 } });
    expect(seen).toContain("open");
    live.stop();
  });

  it("replaces the snapshot instead of changing it", () => {
    const live = client();
    live.start();
    last().open();
    const before = live.getSnapshot();
    last().send(state(1));
    expect(live.getSnapshot()).not.toBe(before);
    expect(before.state).toBeNull();
    live.stop();
  });

  it("tracks whether the vision service is reachable", () => {
    const live = client();
    live.start();
    last().open();
    last().send(state(1));
    last().send({ type: "offline" });
    expect(live.getSnapshot().visionOnline).toBe(false);
    expect(live.getSnapshot().state?.frame).toBe(1); // the last picture is kept
    last().send(state(2));
    expect(live.getSnapshot().visionOnline).toBe(true);
    live.stop();
  });

  it("ignores messages it cannot read", () => {
    const live = client();
    live.start();
    last().open();
    last().send("not json");
    last().send({ type: "mystery" });
    expect(live.getSnapshot().state).toBeNull();
    live.stop();
  });

  it("reconnects with growing pauses and starts over after a success", () => {
    const live = client();
    live.start();
    const waits: number[] = [];
    for (let i = 0; i < 5; i += 1) {
      last().drop();
      expect(live.getSnapshot().connection).toBe("retrying");
      const count = FakeSocket.instances.length;
      let waited = 0;
      while (FakeSocket.instances.length === count) {
        vi.advanceTimersByTime(500);
        waited += 500;
      }
      waits.push(waited);
    }
    expect(waits).toEqual([1000, 2000, 4000, 5000, 5000]); // growing, then capped
    last().open();
    last().drop();
    const count = FakeSocket.instances.length;
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.instances.length).toBe(count + 1); // one second again
    live.stop();
  });

  it("drops a socket that has gone quiet and opens a new one", () => {
    const live = client();
    live.start();
    const first = last();
    first.open();
    vi.advanceTimersByTime(4000);
    first.send(state(1)); // any message resets the watch
    vi.advanceTimersByTime(4999);
    expect(first.closed).toBe(false);
    vi.advanceTimersByTime(1);
    expect(first.closed).toBe(true);
    expect(live.getSnapshot().connection).toBe("retrying");
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.instances).toHaveLength(2);
    live.stop();
  });

  it("closes while paused and reconnects at once when resumed", () => {
    const live = client();
    live.start();
    const first = last();
    first.open();
    live.setPaused(true);
    expect(first.closed).toBe(true);
    expect(live.getSnapshot().connection).toBe("paused");
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1); // nothing is opened while paused
    live.setPaused(false);
    expect(FakeSocket.instances).toHaveLength(2);
    live.stop();
  });

  it("tells its owner when the server refuses the socket", () => {
    const onRefused = vi.fn();
    const live = client({ onRefused });
    live.start();
    last().drop(1008);
    expect(onRefused).toHaveBeenCalledTimes(1);
    last().open();
    last().drop(1006);
    expect(onRefused).toHaveBeenCalledTimes(1);
    live.stop();
  });

  it("stops for good", () => {
    const live = client();
    live.start();
    const socket = last();
    socket.open();
    live.stop();
    expect(socket.closed).toBe(true);
    expect(live.getSnapshot().connection).toBe("closed");
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
    live.setPaused(true);
    live.setPaused(false);
    expect(FakeSocket.instances).toHaveLength(1);
  });

  it("starts paused when the page is already hidden", () => {
    const live = client();
    live.setPaused(true);
    live.start();
    expect(FakeSocket.instances).toHaveLength(0);
    live.setPaused(false);
    expect(FakeSocket.instances).toHaveLength(1);
    live.stop();
  });

  it("retries when a socket cannot even be created", () => {
    let attempts = 0;
    const live = new LiveClient({
      url: () => "ws://pi/api/live",
      createSocket: (url) => {
        attempts += 1;
        if (attempts === 1) throw new Error("blocked");
        return new FakeSocket(url);
      },
    });
    live.start();
    expect(live.getSnapshot().connection).toBe("retrying");
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.instances).toHaveLength(1);
    live.stop();
  });
});
