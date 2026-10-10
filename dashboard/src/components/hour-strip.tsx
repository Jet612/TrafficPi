"use client";

import { useRef, useState } from "react";
import { cn } from "@/lib/cn";
import { hourLabel } from "@/lib/format";
import { spanBetween, type HourSpan } from "@/lib/trails";
import type { TrailHour } from "@/lib/types";

/**
 * The hours of a day as a row of small bars (how many vehicles began a trail in each), which also
 * pick the hours to look at: click one, drag across several, Shift+click to extend, or Tab to the
 * row and use the arrow keys (Shift with them extends), Home and End. A press with the keyboard
 * (Enter or Space) picks the one hour.
 */
export function HourStrip({
  hours,
  counts,
  span,
  labelFor,
  onSelect,
  describedBy,
}: {
  hours: readonly TrailHour[];
  /** Per hour, for the kinds that are shown. */
  counts: readonly number[];
  span: HourSpan;
  /** The spoken name of an hour's button. */
  labelFor: (index: number) => string;
  /** The visitor picked these hours. */
  onSelect: (span: HourSpan) => void;
  /** The id of the text that says how to use the row. */
  describedBy?: string;
}) {
  const anchor = useRef(span.from);
  const dragging = useRef(false);
  const rowRef = useRef<HTMLDivElement>(null);
  const [focused, setFocused] = useState<number | null>(null);
  const last = hours.length - 1;
  const tabbable = Math.min(Math.max(focused ?? span.from, 0), last);
  const busiest = Math.max(1, ...counts);
  // With every hour picked there is nothing to tell apart: they all look the same, as if none were.
  const everything = span.from <= 0 && span.to >= last;

  const indexAt = (event: { clientX: number; clientY: number }): number | null => {
    const button = document.elementFromPoint(event.clientX, event.clientY)?.closest("[data-hour]");
    return button instanceof HTMLElement ? Number(button.dataset.hour) : null;
  };

  const moveFocus = (index: number) => {
    setFocused(index);
    rowRef.current?.querySelector<HTMLElement>(`[data-hour="${index}"]`)?.focus();
  };

  return (
    <div
      ref={rowRef}
      role="group"
      aria-label="Hours of the day"
      aria-describedby={describedBy}
      data-testid="hour-strip"
      className="grid touch-pan-y select-none grid-cols-12 gap-1 sm:[grid-template-columns:repeat(var(--hours),minmax(0,1fr))]"
      style={{ "--hours": hours.length } as React.CSSProperties}
      onPointerDown={(event) => {
        if (event.button !== 0) return;
        const index = indexAt(event);
        if (index === null) return;
        if (!event.shiftKey) anchor.current = index;
        dragging.current = true;
        event.currentTarget.setPointerCapture(event.pointerId);
        setFocused(index);
        onSelect(spanBetween(anchor.current, index));
      }}
      onPointerMove={(event) => {
        if (!dragging.current) return;
        const index = indexAt(event);
        if (index !== null) onSelect(spanBetween(anchor.current, index));
      }}
      onPointerUp={() => {
        dragging.current = false;
      }}
      onPointerCancel={() => {
        dragging.current = false;
      }}
      onKeyDown={(event) => {
        const keys: Record<string, number> = {
          ArrowRight: Math.min(last, tabbable + 1),
          ArrowLeft: Math.max(0, tabbable - 1),
          Home: 0,
          End: last,
        };
        const target = keys[event.key];
        if (target === undefined) return;
        event.preventDefault();
        moveFocus(target);
        if (event.shiftKey) onSelect(spanBetween(anchor.current, target));
      }}
    >
      {hours.map((hour, index) => {
        const count = counts[index] ?? 0;
        const selected = index >= span.from && index <= span.to;
        const marked = selected && !everything;
        return (
          <button
            key={hour.t}
            type="button"
            data-hour={index}
            aria-pressed={selected}
            aria-label={labelFor(index)}
            title={labelFor(index)}
            tabIndex={index === tabbable ? 0 : -1}
            onFocus={() => setFocused(index)}
            onClick={(event) => {
              // A press of the mouse or a finger was dealt with on the way down; this is Enter or Space.
              if (event.detail !== 0) return;
              if (!event.shiftKey) anchor.current = index;
              onSelect(spanBetween(anchor.current, index));
            }}
            className={cn(
              "flex h-16 min-w-0 flex-col rounded-md border px-0.5 pb-1 pt-1 text-xs leading-none tabular-nums transition-colors",
              marked ? "border-accent bg-accent-wash text-ink" : "border-edge bg-surface text-ink-2 hover:bg-sunken",
            )}
          >
            <span className="flex w-full flex-1 items-end" aria-hidden="true">
              <span
                className={cn("block w-full rounded-[3px]", selected ? "bg-accent" : "bg-ink-3/60")}
                style={{ height: count === 0 ? "2px" : `${Math.max(10, Math.round((100 * count) / busiest))}%` }}
              />
            </span>
            <span className="mt-1 block text-center" aria-hidden="true">
              {hourLabel(hour.hour)}
            </span>
          </button>
        );
      })}
    </div>
  );
}
