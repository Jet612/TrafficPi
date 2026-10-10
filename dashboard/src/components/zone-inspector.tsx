"use client";

import { useId } from "react";
import { Ban, CircleAlert, FlipHorizontal2, Octagon, Pentagon, Plus, RotateCcw, Route, Trash2, TriangleAlert } from "lucide-react";
import { NumberField } from "@/components/number-field";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { classColor, classLabel } from "@/lib/classes";
import {
  canAddCorner,
  deleteElement,
  flipLine,
  flipStop,
  renameElement,
  resetStopNumbers,
  setStopNumber,
  splitLongestEdge,
  toggleLineClass,
  toggleStopClass,
} from "@/lib/editor";
import {
  COUNTABLE_CLASSES,
  KIND_LABEL,
  MAX_NAME_LENGTH,
  STOPPABLE_CLASSES,
  STOP_NUMBERS,
  areaPercent,
  displayName,
  elements,
  findElement,
  refOf,
  sameRef,
  type Element,
  type ElementRef,
  type Problem,
} from "@/lib/profile";
import { cn } from "@/lib/cn";
import type { Profile } from "@/lib/types";

const KIND_ICON = { line: Route, detection: Pentagon, exclusion: Ban, stop: Octagon } as const;

export interface ZoneInspectorProps {
  profile: Profile;
  selection: ElementRef | null;
  problems: readonly Problem[];
  onSelect: (ref: ElementRef | null) => void;
  /** Typing: shown at once, undone as one step when the field is left. */
  onLive: (profile: Profile) => void;
  onSettle: () => void;
  /** One undoable change. */
  onChange: (profile: Profile) => void;
}

/** What an element is, in a few words: what a line counts, or how big a zone is. */
function summary(element: Element): string {
  if (element.kind === "stop") {
    const classes = element.value.classes;
    return classes.length === 0 ? "no kind has to stop yet" : `${classes.map((c) => classLabel(c).toLowerCase()).join(", ")} must stop`;
  }
  if (element.kind === "line") {
    const classes = element.value.classes;
    return classes === null ? "counts every kind" : classes.length === 0 ? "counts nothing yet" : `counts ${classes.map((c) => classLabel(c).toLowerCase()).join(", ")}`;
  }
  return `${element.value.points.length} corners, ${areaPercent(element.value)}% of the picture`;
}

export function ZoneInspector({ profile, selection, problems, onSelect, onLive, onSettle, onChange }: ZoneInspectorProps) {
  const all = elements(profile);
  const selected = selection ? findElement(profile, selection) : undefined;
  const problemsOf = (ref: ElementRef) => problems.filter((problem) => sameRef(problem.ref, ref));

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {all.length === 0 ? (
        <p className="rounded-lg border border-dashed border-baseline p-3 text-sm text-ink-2">
          Nothing is drawn yet. Add a counting line across the road to start counting vehicles, or a stop sign to see whether they stop.
        </p>
      ) : (
        <ul aria-label="Lines and zones" className="flex flex-col gap-1.5">
          {all.map((element) => {
            const ref = refOf(element);
            const Icon = KIND_ICON[element.kind];
            const isSelected = sameRef(selection, ref);
            const own = problemsOf(ref);
            const worst = own.find((p) => p.level === "error") ?? own[0];
            return (
              <li key={`${ref.kind}:${ref.id}`}>
                <button
                  type="button"
                  aria-current={isSelected ? "true" : undefined}
                  onClick={() => onSelect(isSelected ? null : ref)}
                  className={cn(
                    "flex w-full items-start gap-2.5 rounded-lg border px-3 py-2 text-left text-sm transition-colors",
                    isSelected ? "border-accent bg-accent-wash text-ink" : "border-edge bg-surface text-ink hover:bg-sunken",
                  )}
                >
                  <Icon className="mt-0.5 size-4 shrink-0 text-ink-2" aria-hidden="true" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{displayName(profile, ref)}</span>
                    <span className="block text-ink-2">
                      {KIND_LABEL[element.kind]}, {summary(element)}
                    </span>
                    {worst && (
                      <span className="mt-1 flex items-start gap-1.5 text-ink">
                        {worst.level === "error" ? (
                          <CircleAlert className="mt-0.5 size-4 shrink-0 text-critical" aria-hidden="true" />
                        ) : (
                          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
                        )}
                        <span>{worst.level === "error" ? "Needs fixing: " : "Check: "}{problemText(worst.message)}</span>
                      </span>
                    )}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

      {selected && (
        <Selected
          key={`${selected.kind}:${selected.value.id}`}
          profile={profile}
          element={selected}
          onLive={onLive}
          onSettle={onSettle}
          onChange={onChange}
          onDeleted={() => onSelect(null)}
        />
      )}
    </div>
  );
}

/** A message names its element first ("Gate: ..."); the list row already does, so drop it there. */
function problemText(message: string): string {
  const colon = message.indexOf(": ");
  return colon === -1 ? message : message.slice(colon + 2);
}

function Selected({
  profile,
  element,
  onLive,
  onSettle,
  onChange,
  onDeleted,
}: {
  profile: Profile;
  element: Element;
  onLive: (profile: Profile) => void;
  onSettle: () => void;
  onChange: (profile: Profile) => void;
  onDeleted: () => void;
}) {
  const nameId = useId();
  const ref = refOf(element);
  const split = splitLongestEdge(profile, ref);

  return (
    <section aria-label={`${displayName(profile, ref)}: properties`} className="rounded-lg border border-edge bg-sunken p-3">
      <Label htmlFor={nameId}>Name</Label>
      <Input
        id={nameId}
        value={element.value.name}
        maxLength={MAX_NAME_LENGTH}
        placeholder={displayName(profile, ref)}
        autoComplete="off"
        onChange={(event) => onLive(renameElement(profile, ref, event.target.value))}
        onBlur={onSettle}
      />

      {element.kind === "stop" ? (
        <StopSignProperties profile={profile} element={element} onLive={onLive} onSettle={onSettle} onChange={onChange} />
      ) : element.kind === "line" ? (
        <>
          <fieldset className="mt-3">
            <legend className="mb-1.5 text-sm font-medium text-ink">Counts</legend>
            <div className="flex flex-wrap gap-x-4 gap-y-1">
              {COUNTABLE_CLASSES.map((name) => {
                const counted = element.value.classes === null || element.value.classes.includes(name);
                return (
                  <label key={name} className="inline-flex min-h-8 cursor-pointer items-center gap-2 text-sm text-ink">
                    <input
                      type="checkbox"
                      checked={counted}
                      onChange={() => onChange(toggleLineClass(profile, element.value.id, name))}
                      className="size-4 accent-[var(--accent)]"
                    />
                    <span aria-hidden="true" className="size-2.5 rounded-full" style={{ background: classColor(name) }} />
                    {classLabel(name)}
                  </label>
                );
              })}
            </div>
          </fieldset>
          <p className="mt-3 text-sm text-ink-2">
            The arrow on the line points the way that counts as forward.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button size="sm" onClick={() => onChange(flipLine(profile, element.value.id))}>
              <FlipHorizontal2 className="size-4" aria-hidden="true" />
              Flip direction
            </Button>
          </div>
        </>
      ) : (
        <>
          <p className="mt-3 text-sm text-ink-2">
            {element.kind === "exclusion"
              ? "Vehicles standing in this area are ignored."
              : "Vehicles are only looked for inside detection zones, when there are any."}{" "}
            Drag a corner to reshape it, or the small dot on an edge to add a corner. Press a corner twice to remove it.
          </p>
          <div className="mt-2 flex flex-wrap gap-2">
            <Button
              size="sm"
              disabled={split === null || !canAddCorner(profile, ref)}
              onClick={() => split && onChange(split.profile)}
            >
              <Plus className="size-4" aria-hidden="true" />
              Add a corner
            </Button>
          </div>
        </>
      )}

      <div className="mt-3 border-t border-edge pt-3">
        <Button
          size="sm"
          onClick={() => {
            onChange(deleteElement(profile, ref));
            onDeleted();
          }}
        >
          <Trash2 className="size-4" aria-hidden="true" />
          Delete this {KIND_LABEL[element.kind].toLowerCase()}
        </Button>
      </div>
    </section>
  );
}

/** The "what must stop, which way, and what counts as a stop" part of a stop sign's panel. */
function StopSignProperties({
  profile,
  element,
  onLive,
  onSettle,
  onChange,
}: {
  profile: Profile;
  element: Extract<Element, { kind: "stop" }>;
  onLive: (profile: Profile) => void;
  onSettle: () => void;
  onChange: (profile: Profile) => void;
}) {
  const sign = element.value;
  const ref = refOf(element);
  const split = splitLongestEdge(profile, ref);
  return (
    <>
      <fieldset className="mt-3">
        <legend className="mb-1.5 text-sm font-medium text-ink">Must stop</legend>
        <div className="flex flex-wrap gap-x-4 gap-y-1">
          {STOPPABLE_CLASSES.map((name) => (
            <label key={name} className="inline-flex min-h-8 cursor-pointer items-center gap-2 text-sm text-ink">
              <input
                type="checkbox"
                checked={sign.classes.includes(name)}
                onChange={() => onChange(toggleStopClass(profile, sign.id, name))}
                className="size-4 accent-[var(--accent)]"
              />
              <span aria-hidden="true" className="size-2.5 rounded-full" style={{ background: classColor(name) }} />
              {classLabel(name)}
            </label>
          ))}
        </div>
      </fieldset>
      <p className="mt-3 text-sm text-ink-2">
        The <strong className="font-medium text-ink">red stripe</strong> is the stop line, and its arrow points the way through the sign. A vehicle is judged
        as it crosses the line: did it stand still for a moment anywhere in the <strong className="font-medium text-ink">red area</strong> before it?
        Drag a corner of the area to reshape it, or an end of the line to move it.
      </p>
      <div className="mt-2 flex flex-wrap gap-2">
        <Button size="sm" onClick={() => onChange(flipStop(profile, sign.id))}>
          <FlipHorizontal2 className="size-4" aria-hidden="true" />
          Flip direction
        </Button>
        <Button size="sm" disabled={split === null || !canAddCorner(profile, ref)} onClick={() => split && onChange(split.profile)}>
          <Plus className="size-4" aria-hidden="true" />
          Add a corner
        </Button>
      </div>

      <details className="mt-3 rounded-lg border border-edge bg-surface px-3 py-2">
        <summary className="cursor-pointer text-sm font-medium text-ink">What counts as a stop</summary>
        <div className="mt-3 grid gap-3">
          <NumberField
            label="Standing still for at least"
            unit="seconds"
            hint="Longer is stricter."
            value={sign.hold_s}
            min={STOP_NUMBERS.hold_s.min}
            max={STOP_NUMBERS.hold_s.max}
            step={0.1}
            onChange={(value) => onLive(setStopNumber(profile, sign.id, "hold_s", value))}
            onCommit={onSettle}
          />
          <NumberField
            label="Moving less than"
            unit="% of its own size"
            hint="While still: smaller is stricter."
            value={sign.still_radius}
            scale={100}
            min={STOP_NUMBERS.still_radius.min}
            max={STOP_NUMBERS.still_radius.max}
            step={1}
            onChange={(value) => onLive(setStopNumber(profile, sign.id, "still_radius", value))}
            onCommit={onSettle}
          />
          <NumberField
            label="Rolling when slower than"
            unit="sizes a second"
            hint="Below this, but never still, is a roll. A size is about 2.5 m for a car, so 0.7 is about 1.75 m a second, or 4 mph."
            value={sign.roll_speed}
            min={STOP_NUMBERS.roll_speed.min}
            max={STOP_NUMBERS.roll_speed.max}
            step={0.1}
            onChange={(value) => onLive(setStopNumber(profile, sign.id, "roll_speed", value))}
            onCommit={onSettle}
          />
          <NumberField
            label="Watched for at least"
            unit="seconds"
            hint="Seen for less than this before the line, a vehicle cannot be judged."
            value={sign.min_observed_s}
            min={STOP_NUMBERS.min_observed_s.min}
            max={STOP_NUMBERS.min_observed_s.max}
            step={0.1}
            onChange={(value) => onLive(setStopNumber(profile, sign.id, "min_observed_s", value))}
            onCommit={onSettle}
          />
          <div>
            <Button size="sm" onClick={() => onChange(resetStopNumbers(profile, sign.id))}>
              <RotateCcw className="size-4" aria-hidden="true" />
              Back to the usual
            </Button>
          </div>
        </div>
      </details>
    </>
  );
}
