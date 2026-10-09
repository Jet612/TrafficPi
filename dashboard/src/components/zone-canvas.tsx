"use client";

import { useEffect, useId, useRef, useState } from "react";
import { ImageOff } from "lucide-react";
import {
  canAddCorner,
  canRemoveCorner,
  edgeMiddle,
  insertCorner,
  moveElement,
  movePoint,
  pointsOf,
  removeCorner,
} from "@/lib/editor";
import { distance, toFraction, toPixels, type Point, type Size } from "@/lib/geometry";
import { COMPACT_BELOW_PX, arrowHead, directionArrow } from "@/lib/overlay";
import { displayName, elements, refOf, sameRef, type Element, type ElementRef, type Problem } from "@/lib/profile";
import type { Profile } from "@/lib/types";

/** A press that moves less than this many pixels is a click, not a drag. */
const DRAG_THRESHOLD_PX = 3;
/** Two presses on one corner within this time remove it. */
const DOUBLE_PRESS_MS = 400;
const HANDLE_RADIUS = 6;
const RING_RADIUS = 11;
/** The invisible, larger target around a handle, for fingers. */
const TOUCH_RADIUS = 15;
/** How far one arrow-key press moves a corner, in pixels (with Shift, ten times as far). */
const NUDGE_PX = 1;
/** Arrow-key presses closer together than this are one step to undo. */
const NUDGE_RUN_MS = 600;

const INK = "#0b0b0b";
const PAPER = "#ffffff";

type Gesture =
  | { kind: "corner"; ref: ElementRef; index: number; base: Profile; startedAt: Point; moved: boolean }
  | { kind: "body"; ref: ElementRef; origin: Point; base: Profile; startedAt: Point; moved: boolean };

const cornerKey = (ref: ElementRef, index: number) => `${ref.kind}:${ref.id}:${index}`;

type ZoneElement = Extract<Element, { kind: "detection" | "exclusion" }>;
type LineElement = Extract<Element, { kind: "line" }>;
const isZone = (element: Element): element is ZoneElement => element.kind !== "line";
const isLine = (element: Element): element is LineElement => element.kind === "line";

export interface ZoneCanvasProps {
  profile: Profile;
  selection: ElementRef | null;
  problems: readonly Problem[];
  /** The picture to draw on; null when there is none. */
  pictureUrl: string | null;
  /** What to tell the visitor when there is no picture. */
  noPictureText: string;
  onPictureError: () => void;
  onSelect: (ref: ElementRef | null) => void;
  /** Part of a gesture: show this now; it is undone together with the rest of the gesture. */
  onLive: (profile: Profile) => void;
  onSettle: () => void;
  /** One undoable change. */
  onChange: (profile: Profile) => void;
}

/**
 * The camera picture with the profile drawn over it, and the means to change it: drag a corner or
 * the end of a line, drag a shape to move it, drag the small dot on a zone's edge to add a corner,
 * press a corner twice (or Delete on it) to remove it, and the arrow keys to nudge a focused
 * corner. Everything is in fractions of the picture; the SVG only converts to pixels to draw.
 */
export function ZoneCanvas(props: ZoneCanvasProps) {
  const { profile, selection, problems, pictureUrl } = props;
  const frameRef = useRef<HTMLDivElement>(null);
  const svgRef = useRef<SVGSVGElement>(null);
  const gesture = useRef<Gesture | null>(null);
  const lastPress = useRef<{ key: string; at: number } | null>(null);
  const nudgeTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const [size, setSize] = useState<Size>({ width: 0, height: 0 });
  const [aspect, setAspect] = useState(16 / 9);
  const hatch = `hatch-${useId().replace(/[^a-z0-9]/gi, "")}`;

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => {
      const box = entry?.contentRect;
      if (box) setSize({ width: box.width, height: box.height });
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  useEffect(
    () => () => {
      if (nudgeTimer.current) clearTimeout(nudgeTimer.current);
    },
    [],
  );

  const clientPoint = (event: { clientX: number; clientY: number }): Point => [event.clientX, event.clientY];

  const fractionAt = (event: { clientX: number; clientY: number }): Point => {
    const box = svgRef.current?.getBoundingClientRect();
    if (!box) return [0, 0];
    return toFraction([event.clientX - box.left, event.clientY - box.top], { width: box.width, height: box.height });
  };

  const begin = (event: React.PointerEvent, next: Gesture) => {
    event.stopPropagation();
    // The picture takes the focus, so that Delete, Escape and undo reach the editor after a click.
    svgRef.current?.focus({ preventScroll: true });
    svgRef.current?.setPointerCapture(event.pointerId);
    gesture.current = next;
  };

  const pressBody = (event: React.PointerEvent, ref: ElementRef) => {
    if (event.button !== 0) return;
    props.onSelect(ref);
    begin(event, { kind: "body", ref, origin: fractionAt(event), base: profile, startedAt: clientPoint(event), moved: false });
  };

  const pressCorner = (event: React.PointerEvent, ref: ElementRef, index: number) => {
    if (event.button !== 0) return;
    event.stopPropagation();
    const key = cornerKey(ref, index);
    const previous = lastPress.current;
    lastPress.current = null;
    if (previous?.key === key && event.timeStamp - previous.at < DOUBLE_PRESS_MS && canRemoveCorner(profile, ref)) {
      props.onChange(removeCorner(profile, ref, index));
      return;
    }
    begin(event, { kind: "corner", ref, index, base: profile, startedAt: clientPoint(event), moved: false });
  };

  const pressMiddle = (event: React.PointerEvent, ref: ElementRef, index: number) => {
    if (event.button !== 0) return;
    const grown = insertCorner(profile, ref, index, edgeMiddle(pointsOf(profile, ref), index));
    if (grown === profile) return;
    props.onLive(grown);
    begin(event, { kind: "corner", ref, index: index + 1, base: grown, startedAt: clientPoint(event), moved: true });
  };

  const onPointerMove = (event: React.PointerEvent<SVGSVGElement>) => {
    const g = gesture.current;
    if (!g) return;
    if (!g.moved) {
      if (distance(clientPoint(event), g.startedAt) < DRAG_THRESHOLD_PX) return;
      g.moved = true;
    }
    const at = fractionAt(event);
    props.onLive(
      g.kind === "corner"
        ? movePoint(g.base, g.ref, g.index, at)
        : moveElement(g.base, g.ref, at[0] - g.origin[0], at[1] - g.origin[1]),
    );
  };

  const endGesture = (event: React.PointerEvent<SVGSVGElement>) => {
    const g = gesture.current;
    if (!g) return;
    gesture.current = null;
    const svg = svgRef.current;
    if (svg?.hasPointerCapture(event.pointerId)) svg.releasePointerCapture(event.pointerId);
    if (g.kind === "corner" && !g.moved && event.type === "pointerup") {
      lastPress.current = { key: cornerKey(g.ref, g.index), at: event.timeStamp };
    }
    props.onSettle();
  };

  const onCornerKey = (event: React.KeyboardEvent, ref: ElementRef, index: number) => {
    const arrows: Record<string, Point> = { ArrowLeft: [-1, 0], ArrowRight: [1, 0], ArrowUp: [0, -1], ArrowDown: [0, 1] };
    const direction = arrows[event.key];
    if (direction) {
      event.preventDefault();
      const current = pointsOf(profile, ref)[index];
      if (!current || size.width === 0 || size.height === 0) return;
      const step = NUDGE_PX * (event.shiftKey ? 10 : 1);
      props.onLive(
        movePoint(profile, ref, index, [current[0] + (direction[0] * step) / size.width, current[1] + (direction[1] * step) / size.height]),
      );
      if (nudgeTimer.current) clearTimeout(nudgeTimer.current);
      nudgeTimer.current = setTimeout(props.onSettle, NUDGE_RUN_MS);
    } else if ((event.key === "Delete" || event.key === "Backspace") && canRemoveCorner(profile, ref)) {
      event.preventDefault();
      event.stopPropagation();
      props.onChange(removeCorner(profile, ref, index));
    }
  };

  const all = elements(profile);
  const zones = all.filter(isZone);
  const lines = all.filter(isLine);
  const selected = selection ? all.find((element) => sameRef(refOf(element), selection)) : undefined;
  const hasError = (ref: ElementRef) => problems.some((problem) => problem.level === "error" && sameRef(problem.ref, ref));
  const px = (point: Point): Point => toPixels(point, size);
  // On a small picture (a phone) the names would pile up on one another: the list has them all, so
  // only the selected shape is labelled there.
  const compact = size.width < COMPACT_BELOW_PX;

  return (
    <div
      ref={frameRef}
      className="relative w-full select-none overflow-hidden rounded-lg bg-[#0b0b0b]"
      style={{ aspectRatio: aspect }}
    >
      {pictureUrl ? (
        // The picture is a still from the API, so a plain <img> is right (and Next's component is not).
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={pictureUrl}
          src={pictureUrl}
          alt=""
          draggable={false}
          className="pointer-events-none absolute inset-0 size-full object-fill"
          onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget;
            if (naturalWidth > 0 && naturalHeight > 0) setAspect(naturalWidth / naturalHeight);
          }}
          onError={props.onPictureError}
        />
      ) : (
        <div className="pointer-events-none absolute inset-0 grid place-items-center p-6 text-center">
          <div className="max-w-xs text-[#c3c2b7]">
            <ImageOff className="mx-auto mb-2 size-7" aria-hidden="true" />
            <p className="font-medium text-white">No picture</p>
            <p className="mt-1 text-sm">{props.noPictureText}</p>
          </div>
        </div>
      )}

      {size.width > 0 && size.height > 0 && (
        <svg
          ref={svgRef}
          data-testid="zone-canvas"
          role="group"
          aria-label="Camera picture with the lines and zones drawn on it"
          viewBox={`0 0 ${size.width} ${size.height}`}
          tabIndex={-1}
          className="absolute inset-0 size-full outline-none"
          onPointerMove={onPointerMove}
          onPointerUp={endGesture}
          onPointerCancel={endGesture}
        >
          <defs>
            <pattern id={hatch} patternUnits="userSpaceOnUse" width="9" height="9" patternTransform="rotate(45)">
              <rect width="9" height="9" fill="rgba(11, 11, 11, 0.42)" />
              <line x1="0" y1="0" x2="0" y2="9" stroke="rgba(255, 255, 255, 0.55)" strokeWidth="2.5" />
            </pattern>
          </defs>

          <rect
            width={size.width}
            height={size.height}
            fill="transparent"
            onPointerDown={() => {
              svgRef.current?.focus({ preventScroll: true });
              props.onSelect(null);
            }}
          />

          {zones.map((element) => {
            const ref = refOf(element);
            const zone = element.value;
            const corners = zone.points.map(px);
            const outline = corners.map(([x, y]) => `${x},${y}`).join(" ");
            const isSelected = sameRef(selection, ref);
            const bad = hasError(ref);
            const left = Math.min(...corners.map((c) => c[0]));
            const top = Math.min(...corners.map((c) => c[1]));
            return (
              <g key={`${ref.kind}:${ref.id}`} data-element={`${ref.kind}:${ref.id}`}>
                <polygon
                  points={outline}
                  fill={element.kind === "exclusion" ? `url(#${hatch})` : `rgba(255, 255, 255, ${isSelected ? 0.16 : 0.08})`}
                  stroke={INK}
                  strokeWidth={isSelected ? 5.5 : 4}
                  strokeLinejoin="round"
                  style={{ cursor: "move", touchAction: "none" }}
                  onPointerDown={(event) => pressBody(event, ref)}
                />
                <polygon
                  points={outline}
                  fill="none"
                  stroke={bad ? "var(--critical)" : PAPER}
                  strokeWidth={isSelected ? 3 : 2}
                  strokeDasharray="7 5"
                  strokeLinejoin="round"
                  pointerEvents="none"
                />
                {(!compact || isSelected) && (
                  <Label x={left + 5} y={top + 16}>
                    {element.kind === "exclusion" ? `Ignored: ${displayName(profile, ref)}` : displayName(profile, ref)}
                  </Label>
                )}
              </g>
            );
          })}

          {lines.map((element) => {
            const ref = refOf(element);
            const line = element.value;
            const a = px(line.a);
            const b = px(line.b);
            const arrow = directionArrow(line, size);
            const [wingOne, wingTwo] = arrowHead(arrow.from, arrow.to);
            const path =
              `M${a[0]} ${a[1]} L${b[0]} ${b[1]} M${arrow.from[0]} ${arrow.from[1]} L${arrow.to[0]} ${arrow.to[1]} ` +
              `M${arrow.to[0]} ${arrow.to[1]} L${wingOne[0]} ${wingOne[1]} M${arrow.to[0]} ${arrow.to[1]} L${wingTwo[0]} ${wingTwo[1]}`;
            const isSelected = sameRef(selection, ref);
            const bad = hasError(ref);
            return (
              <g key={`line:${ref.id}`} data-element={`line:${ref.id}`}>
                <line
                  x1={a[0]}
                  y1={a[1]}
                  x2={b[0]}
                  y2={b[1]}
                  stroke="transparent"
                  strokeWidth={22}
                  style={{ cursor: "move", touchAction: "none" }}
                  onPointerDown={(event) => pressBody(event, ref)}
                />
                <path d={path} fill="none" stroke={INK} strokeWidth={isSelected ? 6.5 : 5} strokeLinecap="round" pointerEvents="none" />
                <path
                  d={path}
                  fill="none"
                  stroke={bad ? "var(--critical)" : PAPER}
                  strokeWidth={isSelected ? 3.5 : 2.5}
                  strokeLinecap="round"
                  pointerEvents="none"
                />
                {(!compact || isSelected) && (
                  <Label x={Math.min(a[0], b[0]) + 4} y={Math.min(a[1], b[1]) - 8}>
                    {displayName(profile, ref)}
                  </Label>
                )}
              </g>
            );
          })}

          {selected && (
            <Handles
              element={selected}
              profile={profile}
              size={size}
              onPressCorner={pressCorner}
              onPressMiddle={pressMiddle}
              onKeyDown={onCornerKey}
              px={px}
            />
          )}
        </svg>
      )}
    </div>
  );
}

/** Text that reads on any picture: white, with a dark outline painted underneath. */
function Label({ x, y, children }: { x: number; y: number; children: React.ReactNode }) {
  return (
    <text
      x={x}
      y={y}
      fontSize="12"
      fontWeight="600"
      fill={PAPER}
      stroke={INK}
      strokeWidth="3.5"
      strokeLinejoin="round"
      paintOrder="stroke"
      pointerEvents="none"
    >
      {children}
    </text>
  );
}

function Handles({
  element,
  profile,
  size,
  px,
  onPressCorner,
  onPressMiddle,
  onKeyDown,
}: {
  element: Element;
  profile: Profile;
  size: Size;
  px: (point: Point) => Point;
  onPressCorner: (event: React.PointerEvent, ref: ElementRef, index: number) => void;
  onPressMiddle: (event: React.PointerEvent, ref: ElementRef, index: number) => void;
  onKeyDown: (event: React.KeyboardEvent, ref: ElementRef, index: number) => void;
}) {
  const ref = refOf(element);
  const points = pointsOf(profile, ref);
  const name = displayName(profile, ref);
  const isLine = element.kind === "line";
  const addable = canAddCorner(profile, ref);
  const removable = canRemoveCorner(profile, ref);
  if (size.width === 0) return null;
  return (
    <g data-testid="handles">
      {!isLine &&
        addable &&
        points.map((_, index) => {
          const [x, y] = px(edgeMiddle(points, index));
          return (
            <g
              key={`middle-${index}`}
              data-handle="edge"
              data-index={index}
              transform={`translate(${x} ${y})`}
              style={{ cursor: "copy", touchAction: "none" }}
              onPointerDown={(event) => onPressMiddle(event, ref, index)}
            >
              <title>Drag to add a corner</title>
              <circle r={12} fill="transparent" />
              <circle r={4} fill={PAPER} fillOpacity={0.85} stroke={INK} strokeWidth={1.5} />
            </g>
          );
        })}
      {points.map((point, index) => {
        const [x, y] = px(point);
        const label = isLine
          ? `${name}, end ${index === 0 ? "A" : "B"}. Arrow keys move it.`
          : `${name}, corner ${index + 1} of ${points.length}. Arrow keys move it${removable ? ", Delete removes it" : ""}.`;
        return (
          <g
            key={`corner-${index}`}
            data-handle="corner"
            data-index={index}
            transform={`translate(${x} ${y})`}
            role="button"
            tabIndex={0}
            aria-label={label}
            className="group outline-none"
            style={{ cursor: "grab", touchAction: "none" }}
            onPointerDown={(event) => onPressCorner(event, ref, index)}
            onKeyDown={(event) => onKeyDown(event, ref, index)}
          >
            <circle r={TOUCH_RADIUS} fill="transparent" />
            {/* The ring that shows which handle has the keyboard focus. */}
            <g fill="none" className="opacity-0 group-focus-visible:opacity-100" pointerEvents="none">
              <circle r={RING_RADIUS} stroke={INK} strokeWidth={5} />
              <circle r={RING_RADIUS} stroke={PAPER} strokeWidth={2.5} />
            </g>
            <circle r={HANDLE_RADIUS} fill={PAPER} stroke={INK} strokeWidth={2} />
          </g>
        );
      })}
    </g>
  );
}
