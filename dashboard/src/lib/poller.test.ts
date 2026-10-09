import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { Poller } from "./poller";

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

function setup(overrides: { fetcher?: () => Promise<number>; hidden?: () => boolean } = {}) {
  let counter = 0;
  const fetcher = vi.fn(overrides.fetcher ?? (async () => ++counter));
  const onData = vi.fn();
  const onError = vi.fn();
  const poller = new Poller<number>({
    fetcher,
    intervalMs: 1000,
    onData,
    onError,
    isHidden: overrides.hidden ?? (() => false),
  });
  return { poller, fetcher, onData, onError };
}

describe("Poller", () => {
  it("fetches at once, then every interval", async () => {
    const { poller, fetcher, onData } = setup();
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(onData).toHaveBeenLastCalledWith(1);
    await vi.advanceTimersByTimeAsync(3000);
    expect(fetcher).toHaveBeenCalledTimes(4);
    poller.stop();
  });

  it("never runs two requests at once", async () => {
    let release: (value: number) => void = () => {};
    const { poller, fetcher } = setup({ fetcher: () => new Promise<number>((resolve) => (release = resolve)) });
    poller.start();
    await vi.advanceTimersByTimeAsync(5000); // the first request is still open
    expect(fetcher).toHaveBeenCalledTimes(1);
    release(1);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetcher).toHaveBeenCalledTimes(2);
    poller.stop();
  });

  it("waits longer after failures and recovers", async () => {
    let fail = true;
    const { poller, fetcher, onError, onData } = setup({
      fetcher: async () => {
        if (fail) throw new Error("down");
        return 7;
      },
    });
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    expect(onError).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1999); // the wait after one failure is 2 s
    expect(fetcher).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(1);
    expect(fetcher).toHaveBeenCalledTimes(2);
    fail = false;
    await vi.advanceTimersByTimeAsync(4000); // after two failures: 4 s
    expect(onData).toHaveBeenCalledWith(7);
    const calls = fetcher.mock.calls.length;
    await vi.advanceTimersByTimeAsync(1000); // success: back to the normal interval
    expect(fetcher.mock.calls.length).toBe(calls + 1);
    poller.stop();
  });

  it("does not fetch while the page is hidden, and catches up when it is shown", async () => {
    let hidden = true;
    const { poller, fetcher } = setup({ hidden: () => hidden });
    poller.start();
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetcher).not.toHaveBeenCalled();
    hidden = false;
    poller.visibilityChanged();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetcher).toHaveBeenCalledTimes(1);
    poller.stop();
  });

  it("stops for good, and drops an answer that arrives afterwards", async () => {
    let release: (value: number) => void = () => {};
    const { poller, fetcher, onData } = setup({ fetcher: () => new Promise<number>((resolve) => (release = resolve)) });
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    poller.stop();
    release(5);
    await vi.advanceTimersByTimeAsync(10_000);
    expect(onData).not.toHaveBeenCalled();
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("refresh fetches now and keeps the schedule", async () => {
    const { poller, fetcher } = setup();
    poller.start();
    await vi.advanceTimersByTimeAsync(0);
    await vi.advanceTimersByTimeAsync(400);
    poller.refresh();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetcher).toHaveBeenCalledTimes(2);
    await vi.advanceTimersByTimeAsync(1000);
    expect(fetcher).toHaveBeenCalledTimes(3);
    poller.stop();
  });
});
