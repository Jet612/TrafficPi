"use client";

import { Info } from "lucide-react";
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { placeTip } from "@/lib/tip";

/** How long the bubble stays after the pointer has left it: time to cross the gap between the icon and the bubble. */
const LEAVE_MS = 150;

/** Closes the bubble that is open now, so that two are never open at once. */
let closeShown: (() => void) | null = null;

/**
 * An explanation kept out of the way: a small (i) that shows its text in a bubble while the pointer
 * is on it or the keyboard is on it. Pressing it opens the bubble and keeps it open (that is how a
 * finger opens it); a second press, Escape or a press anywhere else closes it. The pointer can move
 * onto the bubble without it going away, so that the text can be selected.
 *
 * The text is always in the page and only hidden while the bubble is shut, so that a screen reader
 * gets it from the button (named only "More information": the text it reads out says what it is
 * about) and from any control that names `id`, the id of the text, in `aria-describedby`, without
 * opening anything. Put in words and phrasing elements (<strong>, <code>), not paragraphs.
 */
export function InfoTip({ children, id, className }: { children: React.ReactNode; id?: string; className?: string }) {
  const generated = useId();
  const textId = id ?? generated;
  const wrapper = useRef<HTMLSpanElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const bubble = useRef<HTMLSpanElement>(null);
  const leaving = useRef<number | null>(null);
  const [hover, setHover] = useState(false); // a mouse or a pen is on the icon or on the bubble
  const [focus, setFocus] = useState(false); // the keyboard has moved onto the icon
  const [pressed, setPressed] = useState(false); // the icon has been pressed, which keeps the bubble open
  const open = hover || focus || pressed;

  const stopLeaving = useCallback(() => {
    if (leaving.current !== null) window.clearTimeout(leaving.current);
    leaving.current = null;
  }, []);
  const close = useCallback(() => {
    stopLeaving();
    setHover(false);
    setFocus(false);
    setPressed(false);
  }, [stopLeaving]);
  useEffect(() => stopLeaving, [stopLeaving]);

  // One bubble at a time.
  useEffect(() => {
    if (!open) return;
    if (closeShown && closeShown !== close) closeShown();
    closeShown = close;
    return () => {
      if (closeShown === close) closeShown = null;
    };
  }, [open, close]);

  // Under the icon, or over it when there is no room, and inside the window; kept so while the page moves.
  useLayoutEffect(() => {
    if (!open) return;
    const place = () => {
      const icon = button.current;
      const text = bubble.current;
      if (!icon || !text) return;
      const root = document.documentElement;
      const at = placeTip(
        icon.getBoundingClientRect(),
        { width: text.offsetWidth, height: text.offsetHeight },
        { width: root.clientWidth, height: root.clientHeight },
      );
      text.style.left = `${at.left}px`;
      text.style.top = `${at.top}px`;
    };
    place();
    window.addEventListener("resize", place);
    window.addEventListener("scroll", place, true); // in the capture phase: a box that scrolls inside the page does not tell the window
    return () => {
      window.removeEventListener("resize", place);
      window.removeEventListener("scroll", place, true);
    };
  }, [open]);

  // Escape closes it without moving the pointer or the keyboard, and is not passed on while it does (an editor behind may use Escape too);
  // a press anywhere else lets go of a pressed one.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      close();
    };
    const onPress = (event: PointerEvent) => {
      if (!(event.target instanceof Node) || !wrapper.current?.contains(event.target)) setPressed(false);
    };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("pointerdown", onPress, true);
    return () => {
      document.removeEventListener("keydown", onKey, true);
      document.removeEventListener("pointerdown", onPress, true);
    };
  }, [open, close]);

  return (
    <span
      ref={wrapper}
      className={cn("inline-flex align-middle", className)}
      // A finger has no hover: its press (below) is what opens the bubble. The browser's imitation of a mouse for a touch is not taken for one.
      onPointerEnter={(event) => {
        if (event.pointerType === "touch") return;
        stopLeaving();
        setHover(true);
      }}
      onPointerLeave={(event) => {
        if (event.pointerType === "touch") return;
        stopLeaving();
        leaving.current = window.setTimeout(() => {
          leaving.current = null;
          setHover(false);
        }, LEAVE_MS);
      }}
    >
      <button
        ref={button}
        type="button"
        aria-label="More information"
        aria-describedby={textId}
        onClick={() => setPressed((was) => !was)}
        // Only the keyboard's arrival opens it by itself: a click also focuses a button in some browsers, and that must not hold it open.
        onFocus={(event) => {
          if (event.currentTarget.matches(":focus-visible")) setFocus(true);
        }}
        onBlur={() => {
          setFocus(false);
          setPressed(false);
        }}
        className={cn("grid size-6 shrink-0 place-items-center rounded-full transition-colors hover:text-ink", open ? "text-ink" : "text-ink-3")}
      >
        <Info className="size-4" aria-hidden="true" />
      </button>
      <span
        ref={bubble}
        id={textId}
        role="tooltip"
        hidden={!open}
        className="fixed z-50 max-h-[calc(100dvh-1rem)] w-max max-w-[min(24rem,calc(100vw-1rem))] overflow-y-auto whitespace-normal rounded-lg border border-baseline bg-sunken px-3 py-2 text-left text-sm font-normal text-ink shadow-lg"
      >
        {children}
      </span>
    </span>
  );
}
