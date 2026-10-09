/**
 * Full screen for one element: the browser's own where there is one, and the caller's fixed overlay
 * where there is not (Safari on an iPhone only offers it for <video>, not for the picture and the
 * boxes drawn over it). The functions take the element and the document as arguments, so that they
 * can be tried without a browser. Safari's prefixed names are used when the plain ones are missing.
 */

export interface FullscreenElement {
  requestFullscreen?: (options?: { navigationUI?: "hide" | "show" | "auto" }) => Promise<void>;
  webkitRequestFullscreen?: () => Promise<void> | void;
}

export interface FullscreenDocument {
  fullscreenEnabled?: boolean;
  webkitFullscreenEnabled?: boolean;
  fullscreenElement?: Element | null;
  webkitFullscreenElement?: Element | null;
  exitFullscreen?: () => Promise<void>;
  webkitExitFullscreen?: () => Promise<void> | void;
}

/** The element that is full screen now, if there is one. */
export function currentFullscreenElement(doc: FullscreenDocument): Element | null {
  return doc.fullscreenElement ?? doc.webkitFullscreenElement ?? null;
}

/** Whether the browser can make this element full screen by itself. */
export function canGoFullscreen(element: FullscreenElement, doc: FullscreenDocument): boolean {
  const allowed = doc.fullscreenEnabled ?? doc.webkitFullscreenEnabled ?? true;
  return allowed && (typeof element.requestFullscreen === "function" || typeof element.webkitRequestFullscreen === "function");
}

/** Ask for full screen. False when the browser cannot or will not: the caller then uses its overlay. */
export async function requestFullscreen(element: FullscreenElement, doc: FullscreenDocument): Promise<boolean> {
  if (!canGoFullscreen(element, doc)) return false;
  try {
    if (typeof element.requestFullscreen === "function") await element.requestFullscreen({ navigationUI: "hide" });
    else await element.webkitRequestFullscreen?.();
    return true;
  } catch {
    return false; // not allowed here (a frame, a policy), or the request was refused
  }
}

/** Leave the browser's full screen, if it is in it. */
export async function leaveFullscreen(doc: FullscreenDocument): Promise<void> {
  if (currentFullscreenElement(doc) === null) return;
  try {
    if (typeof doc.exitFullscreen === "function") await doc.exitFullscreen();
    else await doc.webkitExitFullscreen?.();
  } catch {
    // it left by itself in the meantime
  }
}
