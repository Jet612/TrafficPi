import { describe, expect, it, vi } from "vitest";
import {
  canGoFullscreen,
  currentFullscreenElement,
  leaveFullscreen,
  requestFullscreen,
  type FullscreenDocument,
  type FullscreenElement,
} from "./fullscreen";

const element = (over: FullscreenElement = {}): FullscreenElement => ({ requestFullscreen: vi.fn().mockResolvedValue(undefined), ...over });
const marker = { tagName: "DIV" } as unknown as Element;

describe("which element is full screen", () => {
  it("reads the plain name, then Safari's, and says none when neither is set", () => {
    expect(currentFullscreenElement({ fullscreenElement: marker })).toBe(marker);
    expect(currentFullscreenElement({ webkitFullscreenElement: marker })).toBe(marker);
    expect(currentFullscreenElement({ fullscreenElement: null, webkitFullscreenElement: marker })).toBe(marker);
    expect(currentFullscreenElement({})).toBeNull();
    expect(currentFullscreenElement({ fullscreenElement: null })).toBeNull();
  });
});

describe("whether the browser can do it", () => {
  it("can when the element has the call and the document allows it", () => {
    expect(canGoFullscreen(element(), { fullscreenEnabled: true })).toBe(true);
    expect(canGoFullscreen(element(), {})).toBe(true); // a browser that does not say is asked
    expect(canGoFullscreen({ webkitRequestFullscreen: vi.fn() }, { webkitFullscreenEnabled: true })).toBe(true);
  });

  it("cannot when the element has no such call (an iPhone's Safari for a div)", () => {
    expect(canGoFullscreen({}, { fullscreenEnabled: true })).toBe(false);
  });

  it("cannot when the document forbids it (an embedded frame without permission)", () => {
    expect(canGoFullscreen(element(), { fullscreenEnabled: false })).toBe(false);
    expect(canGoFullscreen(element(), { webkitFullscreenEnabled: false })).toBe(false);
  });
});

describe("asking for full screen", () => {
  it("asks the browser, hiding its own address bar, and says it worked", async () => {
    const target = element();
    expect(await requestFullscreen(target, {})).toBe(true);
    expect(target.requestFullscreen).toHaveBeenCalledWith({ navigationUI: "hide" });
  });

  it("uses Safari's call when the plain one is missing", async () => {
    const webkit = vi.fn();
    expect(await requestFullscreen({ webkitRequestFullscreen: webkit }, {})).toBe(true);
    expect(webkit).toHaveBeenCalledTimes(1);
  });

  it("says no, and does not throw, when the browser has none or refuses", async () => {
    expect(await requestFullscreen({}, {})).toBe(false);
    const refusing = element({ requestFullscreen: vi.fn().mockRejectedValue(new TypeError("Permissions check failed")) });
    expect(await requestFullscreen(refusing, {})).toBe(false);
    const throwing = element({ requestFullscreen: vi.fn(() => { throw new Error("boom"); }) });
    expect(await requestFullscreen(throwing, {})).toBe(false);
    const forbidden = element();
    expect(await requestFullscreen(forbidden, { fullscreenEnabled: false })).toBe(false);
    expect(forbidden.requestFullscreen).not.toHaveBeenCalled();
  });
});

describe("leaving full screen", () => {
  it("exits when something is full screen", async () => {
    const doc: FullscreenDocument = { fullscreenElement: marker, exitFullscreen: vi.fn().mockResolvedValue(undefined) };
    await leaveFullscreen(doc);
    expect(doc.exitFullscreen).toHaveBeenCalledTimes(1);
  });

  it("does nothing when nothing is", async () => {
    const doc: FullscreenDocument = { fullscreenElement: null, exitFullscreen: vi.fn() };
    await leaveFullscreen(doc);
    expect(doc.exitFullscreen).not.toHaveBeenCalled();
  });

  it("uses Safari's call, and survives a browser that has already left", async () => {
    const webkit = vi.fn();
    await leaveFullscreen({ webkitFullscreenElement: marker, webkitExitFullscreen: webkit });
    expect(webkit).toHaveBeenCalledTimes(1);
    await expect(
      leaveFullscreen({ fullscreenElement: marker, exitFullscreen: vi.fn().mockRejectedValue(new TypeError("Document not active")) }),
    ).resolves.toBeUndefined();
  });
});
