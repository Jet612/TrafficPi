import { describe, expect, it } from "vitest";
import { crossingHeading } from "./direction";

describe("crossingHeading", () => {
  it("points a vertical line's forward arrow to its right-hand side, as the video draws it", () => {
    // a above b: the right-hand side of a -> b (y down) is to the left of the screen.
    expect(crossingHeading({ a: [0.3, 0.4], b: [0.3, 0.9] }, true)).toBe("left");
    expect(crossingHeading({ a: [0.3, 0.4], b: [0.3, 0.9] }, false)).toBe("right");
    expect(crossingHeading({ a: [0.3, 0.9], b: [0.3, 0.4] }, true)).toBe("right");
  });

  it("does the same for a horizontal line", () => {
    // a left of b: the right-hand side of a -> b is downwards.
    expect(crossingHeading({ a: [0.1, 0.5], b: [0.9, 0.5] }, true)).toBe("down");
    expect(crossingHeading({ a: [0.1, 0.5], b: [0.9, 0.5] }, false)).toBe("up");
  });

  it("picks the nearest of eight for a slanted line", () => {
    expect(crossingHeading({ a: [0.1, 0.9], b: [0.9, 0.1] }, true)).toBe("down-right");
    expect(crossingHeading({ a: [0.1, 0.9], b: [0.9, 0.1] }, false)).toBe("up-left");
    expect(crossingHeading({ a: [0.1, 0.1], b: [0.9, 0.9] }, true)).toBe("down-left");
    expect(crossingHeading({ a: [0.1, 0.1], b: [0.9, 0.9] }, false)).toBe("up-right");
  });

  it("agrees with the arrow the overlay draws", () => {
    // directionArrow() draws from the middle to (mx - dy/norm * L, my + dx/norm * L): the same vector.
    const line: { a: [number, number]; b: [number, number] } = { a: [0.2, 0.3], b: [0.5, 0.9] };
    const dx = line.b[0] - line.a[0];
    const dy = line.b[1] - line.a[1];
    const drawn = { x: -dy, y: dx };
    expect(drawn.x).toBeLessThan(0); // left on the screen...
    expect(drawn.y).toBeGreaterThan(0); // ...and down
    expect(crossingHeading(line, true)).toBe("down-left");
  });

  it("is never undefined for a line with no length", () => {
    expect(crossingHeading({ a: [0.5, 0.5], b: [0.5, 0.5] }, true)).toBe("right");
    expect(crossingHeading({ a: [0.5, 0.5], b: [0.5, 0.5] }, false)).toBe("left");
  });
});
