/**
 * Where the bubble of an info icon goes: under the icon, or over it when there is no room below, as
 * far across as the window allows and never closer to an edge than `TIP_EDGE`. Plain numbers in and
 * out (the component measures, this decides), so that it can be tried without a browser.
 */

/** A rectangle in window coordinates: what `getBoundingClientRect()` says of the icon. */
export interface TipAnchor {
  left: number;
  top: number;
  width: number;
  height: number;
}

export interface TipSize {
  width: number;
  height: number;
}

/** The bubble's top left corner, in window coordinates. */
export interface TipPlace {
  left: number;
  top: number;
}

/** How far the bubble stands from its icon, and the least it keeps from the window's edge, in pixels. */
export const TIP_GAP = 8;
export const TIP_EDGE = 8;

function clamp(value: number, least: number, most: number): number {
  // A bubble that is bigger than the window cannot keep clear of both edges: the top left one wins.
  return Math.max(least, Math.min(value, most));
}

/** Below the icon if the bubble fits there, else above it if it fits there, else on the roomier side. */
export function placeTip(anchor: TipAnchor, tip: TipSize, viewport: TipSize): TipPlace {
  const roomBelow = viewport.height - (anchor.top + anchor.height) - TIP_GAP - TIP_EDGE;
  const roomAbove = anchor.top - TIP_GAP - TIP_EDGE;
  const below = tip.height <= roomBelow || (tip.height > roomAbove && roomBelow >= roomAbove);
  const top = below ? anchor.top + anchor.height + TIP_GAP : anchor.top - TIP_GAP - tip.height;
  const centered = anchor.left + anchor.width / 2 - tip.width / 2;
  return {
    left: clamp(centered, TIP_EDGE, viewport.width - tip.width - TIP_EDGE),
    top: clamp(top, TIP_EDGE, viewport.height - tip.height - TIP_EDGE),
  };
}
