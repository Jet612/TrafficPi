import type { LiveLine } from "./types";

/** The eight ways an arrow can point on the screen, as the names of the icons that show them. */
export type Heading = "up" | "up-right" | "right" | "down-right" | "down" | "down-left" | "left" | "up-left";

const HEADINGS: readonly Heading[] = ["right", "down-right", "down", "down-left", "left", "up-left", "up", "up-right"];

/**
 * Which way a vehicle goes across a line, as the nearest of eight arrows on the screen. Forward is the
 * way the arrow drawn on the video points: at right angles to the line, towards the right of a -> b
 * (y points down, as in the backend's geometry); reverse is the other way. A list of counts shows the
 * same arrow that the picture does, instead of one that means the same on every line.
 */
export function crossingHeading(line: Pick<LiveLine, "a" | "b">, forward: boolean): Heading {
  const dx = line.b[0] - line.a[0];
  const dy = line.b[1] - line.a[1];
  // The forward arrow points along (-dy, dx).
  let x = -dy;
  let y = dx;
  if (!forward) {
    x = -x;
    y = -y;
  }
  if (x === 0 && y === 0) return forward ? "right" : "left";
  const angle = Math.atan2(y, x); // 0 is right, and y grows downwards, so a quarter turn is down
  const index = Math.round(angle / (Math.PI / 4));
  return HEADINGS[((index % 8) + 8) % 8] ?? "right";
}
