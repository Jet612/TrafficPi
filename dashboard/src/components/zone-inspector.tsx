"use client";

import { useId } from "react";
import { Ban, CircleAlert, FlipHorizontal2, Pentagon, Plus, Route, Trash2, TriangleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { classColor, classLabel } from "@/lib/classes";
import { canAddCorner, deleteElement, flipLine, renameElement, splitLongestEdge, toggleLineClass } from "@/lib/editor";
import {
  COUNTABLE_CLASSES,
  KIND_LABEL,
  MAX_NAME_LENGTH,
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

const KIND_ICON = { line: Route, detection: Pentagon, exclusion: Ban } as const;

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
          Nothing is drawn yet. Add a counting line across the road to start counting vehicles.
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

      {element.kind === "line" ? (
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
              ? "Vehicles standing in this area are ignored, for example parked cars."
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
