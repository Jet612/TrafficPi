"use client";

import { useCallback, useEffect, useState, type RefObject } from "react";
import { currentFullscreenElement, leaveFullscreen, requestFullscreen } from "./fullscreen";

export interface FullscreenControl {
  /** True while the element fills the screen, by the browser's full screen or by the overlay. */
  active: boolean;
  toggle: () => void;
  exit: () => void;
}

/**
 * Full screen for the element in `ref`. The browser's own full screen is used where it works; where
 * it does not (an iPhone's Safari, a page in a frame), `active` is true all the same and the caller
 * lays the element over the whole page. Escape leaves the overlay, as it leaves the real thing, and
 * the page behind it does not scroll while it is up.
 */
export function useFullscreen(ref: RefObject<HTMLElement | null>): FullscreenControl {
  const [native, setNative] = useState(false);
  const [overlay, setOverlay] = useState(false);

  // The browser says when it enters or leaves full screen, by Escape as well as by our button.
  useEffect(() => {
    const onChange = () => setNative(ref.current !== null && currentFullscreenElement(document) === ref.current);
    document.addEventListener("fullscreenchange", onChange);
    document.addEventListener("webkitfullscreenchange", onChange);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      document.removeEventListener("webkitfullscreenchange", onChange);
    };
  }, [ref]);

  useEffect(() => {
    if (!overlay) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOverlay(false);
    };
    document.addEventListener("keydown", onKey);
    const before = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = before;
    };
  }, [overlay]);

  const exit = useCallback(() => {
    setOverlay(false);
    void leaveFullscreen(document);
  }, []);

  const toggle = useCallback(() => {
    if (native || overlay) {
      exit();
      return;
    }
    const element = ref.current;
    if (!element) return;
    void requestFullscreen(element, document).then((worked) => {
      if (!worked) setOverlay(true);
    });
  }, [native, overlay, exit, ref]);

  return { active: native || overlay, toggle, exit };
}
