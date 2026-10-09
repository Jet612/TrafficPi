import { describe, expect, it } from "vitest";
import { STILL_NOTICE, STILL_REFRESH_MS, pictureMode, stillUrl } from "./remote";

describe("the picture for a viewer who comes through Cloudflare", () => {
  it("shows the stream when the server allows it and a still when it does not", () => {
    expect(pictureMode(true)).toBe("stream");
    expect(pictureMode(false)).toBe("still");
  });

  it("asks for a new still each time, so that the browser does not reuse the last", () => {
    expect(stillUrl(0)).toBe("/api/snapshot.jpg?n=0");
    expect(new Set([1, 2, 3].map(stillUrl)).size).toBe(3);
  });

  it("refreshes every few seconds, not many times a second (that would be video)", () => {
    expect(STILL_REFRESH_MS).toBeGreaterThanOrEqual(2000);
    expect(STILL_NOTICE).toContain(`${STILL_REFRESH_MS / 1000} seconds`);
    expect(STILL_NOTICE).toMatch(/Cloudflare/);
  });
});
