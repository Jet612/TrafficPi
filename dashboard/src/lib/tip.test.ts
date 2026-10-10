import { describe, expect, it } from "vitest";
import { TIP_EDGE, TIP_GAP, placeTip } from "./tip";

const viewport = { width: 1000, height: 800 };
const icon = (left: number, top: number) => ({ left, top, width: 24, height: 24 });
const bubble = { width: 300, height: 100 };

describe("where the bubble goes", () => {
  it("is under the icon, a little way off, and centred on it", () => {
    const place = placeTip(icon(400, 200), bubble, viewport);
    expect(place.top).toBe(200 + 24 + TIP_GAP);
    expect(place.left).toBe(412 - 150);
  });

  it("keeps clear of the left and right edges of the window", () => {
    expect(placeTip(icon(2, 200), bubble, viewport).left).toBe(TIP_EDGE);
    expect(placeTip(icon(990, 200), bubble, viewport).left).toBe(viewport.width - bubble.width - TIP_EDGE);
  });

  it("goes over the icon when it does not fit below but fits above", () => {
    const place = placeTip(icon(400, 700), bubble, viewport);
    expect(place.top).toBe(700 - TIP_GAP - bubble.height);
    expect(place.top + bubble.height).toBeLessThan(700);
  });

  it("stays below when it just fits there", () => {
    const top = viewport.height - TIP_EDGE - bubble.height - TIP_GAP - 24; // the icon's top, with the bubble touching the edge's margin
    expect(placeTip(icon(400, top), bubble, viewport).top).toBe(top + 24 + TIP_GAP);
    expect(placeTip(icon(400, top + 1), bubble, viewport).top).toBe(top + 1 - TIP_GAP - bubble.height);
  });

  it("takes the roomier side when it fits on neither, and stays inside the window", () => {
    const tall = { width: 300, height: 700 };
    const nearTop = placeTip(icon(400, 100), tall, viewport); // 84 of room above, 660 below
    const nearBottom = placeTip(icon(400, 650), tall, viewport); // 634 of room above, 110 below
    for (const place of [nearTop, nearBottom]) {
      expect(place.top).toBeGreaterThanOrEqual(TIP_EDGE);
      expect(place.top + tall.height).toBeLessThanOrEqual(viewport.height - TIP_EDGE);
    }
    expect(nearTop.top).toBe(viewport.height - tall.height - TIP_EDGE); // pushed up from below the icon, as far as the window allows
    expect(nearBottom.top).toBe(TIP_EDGE); // pushed down from above the icon
  });

  it("keeps the top left corner in view when the bubble is bigger than the window", () => {
    expect(placeTip(icon(400, 200), { width: 1200, height: 900 }, viewport)).toEqual({ left: TIP_EDGE, top: TIP_EDGE });
  });

  it("works for an icon at the very top of the window (nothing above it)", () => {
    const place = placeTip(icon(400, 0), bubble, viewport);
    expect(place.top).toBe(24 + TIP_GAP);
  });

  it("works in a window as narrow as a phone's", () => {
    const phone = { width: 360, height: 640 };
    const place = placeTip(icon(300, 100), { width: 344, height: 120 }, phone);
    expect(place.left).toBe(TIP_EDGE);
    expect(place.left + 344).toBeLessThanOrEqual(phone.width - TIP_EDGE);
  });
});
