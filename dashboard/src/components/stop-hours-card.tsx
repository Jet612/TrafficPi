"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { X } from "lucide-react";
import { DayButtons } from "@/components/day-buttons";
import { HoursTimeline } from "@/components/hours-timeline";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { HowItWorks } from "@/components/ui/how-it-works";
import { Input, Label } from "@/components/ui/input";
import { OptionGroup } from "@/components/ui/option-group";
import { StatusBadge } from "@/components/ui/status";
import { Switch } from "@/components/ui/switch";
import { TimeField } from "@/components/ui/time-field";
import { useUnsavedWarning } from "@/components/unsaved";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { useRequest } from "@/lib/hooks";
import { daysText } from "@/lib/mail";
import {
  MAX_MARGIN,
  MAX_RANGES,
  MIN_MARGIN,
  addRange,
  dayText,
  fromDraft,
  isChanged,
  noonWarning,
  parseCoordinate,
  parseMargin,
  removeRange,
  savedText,
  statusText,
  timeline,
  toDraft,
  type RangeDraft,
  type StopHoursDraft,
} from "@/lib/stop-hours";
import type { StopHoursState } from "@/lib/types";

const REFRESH_MS = 60_000;
const PREVIEW_DELAY_MS = 400;

type Outcome = { kind: "ok" | "error"; text: string } | null;

/**
 * When stop signs are judged (plan.md D42): only in daylight, which follows the seasons, and/or
 * only at set times of the day on chosen days. Outside the hours a pass is not judged at all. The
 * sun's times for a place are shown as it is typed.
 */
export function StopHoursCard() {
  const [state, setState] = useState<StopHoursState | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  // An answer to something the owner did is newer than a refresh that was already on its way.
  const epoch = useRef(0);
  const apply = useCallback((next: StopHoursState) => {
    epoch.current += 1;
    setState(next);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const load = () => {
      const started = epoch.current;
      api.stopHours(controller.signal).then(
        (next) => {
          if (epoch.current === started) setState(next);
        },
        (error: unknown) => {
          if (controller.signal.aborted) return;
          console.error("could not load the stop-sign hours", error);
          setLoadFailed(true);
        },
      );
    };
    load();
    const timer = setInterval(() => {
      if (!document.hidden) load();
    }, REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Stop-sign hours</CardTitle>
          <CardSubtitle>Choose the hours in which stop signs are judged; outside them a pass is not judged or stored.</CardSubtitle>
        </div>
      </CardHeader>
      <CardBody>
        {state === null ? (
          <p className="text-sm text-ink-2">{loadFailed ? "The stop-sign hours could not be loaded." : "Loading…"}</p>
        ) : (
          <HoursForm state={state} apply={apply} />
        )}
      </CardBody>
    </Card>
  );
}

function HoursForm({ state, apply }: { state: StopHoursState; apply: (next: StopHoursState) => void }) {
  const [edits, setEdits] = useState<Partial<StopHoursDraft>>({});
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const latitudeId = useId();
  const latitudeHintId = useId();
  const longitudeId = useId();
  const longitudeHintId = useId();
  const beforeId = useId();
  const beforeHintId = useId();
  const afterId = useId();
  const afterHintId = useId();

  // What is saved, with what has been typed over it: a refresh of the saved state never undoes an edit.
  const draft: StopHoursDraft = { ...toDraft(state.config), ...edits };
  const checked = fromDraft(draft);
  const problem = checked.ok ? null : checked.problem;
  const dirty = isChanged(draft, state.config);
  useUnsavedWarning(dirty);

  // What the hours being typed would mean, asked of the server a moment after the typing stops.
  const config = dirty && checked.ok ? checked.config : null;
  const preview = useRequest(
    config === null ? null : JSON.stringify(config),
    (signal) => (config === null ? Promise.reject(new Error("nothing to check")) : api.checkStopHours(config, signal)),
    { delayMs: PREVIEW_DELAY_MS, refreshMs: REFRESH_MS },
  );
  const shown = config === null ? state.status : (preview.data ?? state.status);
  const stale = config !== null && preview.loading;
  const line = statusText(shown);
  const warning = shown.day ? noonWarning(shown.day) : null;

  function change(update: Partial<StopHoursDraft>) {
    setOutcome(null);
    setEdits((current) => ({ ...current, ...update }));
  }

  function changeRange(index: number, update: Partial<RangeDraft>) {
    change({ ranges: draft.ranges.map((range, i) => (i === index ? { ...range, ...update } : range)) });
  }

  async function save() {
    if (!checked.ok) return;
    setBusy(true);
    setOutcome(null);
    try {
      const saved = await api.saveStopHours(checked.config);
      apply(saved);
      setEdits({});
      setOutcome({ kind: "ok", text: savedText(saved.vision_notified) });
    } catch (error) {
      setOutcome({ kind: "error", text: error instanceof ApiError ? error.message : "Could not save." });
    } finally {
      setBusy(false);
    }
  }

  const latitudeBad = draft.latitude.trim() !== "" && parseCoordinate(draft.latitude, 90) === null;
  const longitudeBad = draft.longitude.trim() !== "" && parseCoordinate(draft.longitude, 180) === null;
  const beforeBad = parseMargin(draft.before) === null;
  const afterBad = parseMargin(draft.after) === null;
  const heading = dirty && checked.ok ? "Today, if you save these hours" : "Today";
  const strip = timeline(shown, checked.ok ? checked.config : state.config);

  return (
    <form
      aria-label="Stop-sign hours"
      className="grid gap-6"
      noValidate // the form says what is wrong in words; the browser's own bubble would hide that
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <HowItWorks>
        <p>
          Outside the hours a pass is not judged at all: no row in the history, no picture, no clip, nothing on the Data page or in the daily email. Vehicles are still counted at the
          counting lines and their trails still saved.
        </p>
        <p>Switch on one limit or both (a pass must then be inside both); with both off, every pass is judged at every hour.</p>
      </HowItWorks>

      <section aria-label="What these hours mean" aria-busy={stale} className={cn("grid gap-3", stale && "opacity-60")}>
        <p className="text-sm font-medium text-ink">{heading}</p>
        <HoursTimeline line={strip} />
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <StatusBadge level={line.level}>{line.text}</StatusBadge>
          {warning && <StatusBadge level="warning">{warning}</StatusBadge>}
        </div>
        {shown.limited && <p className="text-sm text-ink-2">Stop signs are judged only {shown.description}.</p>}
        {shown.day && <p className="text-sm text-ink-3">{dayText(shown.day, shown.timezone)}</p>}
      </section>

      <div className="grid gap-x-10 gap-y-6 md:grid-cols-2">
        <OptionGroup
          title="In daylight"
          hint="From some minutes before sunrise to some minutes after sunset, where the camera is. It follows the seasons by itself, so the hours are longer in summer and shorter in winter."
        >
          <Switch label="Judge stop signs only in daylight" checked={draft.daylight} onChange={(daylight) => change({ daylight })} />
          <fieldset disabled={!draft.daylight} className={cn("grid gap-3 border-l-2 border-edge pl-4 transition-opacity", !draft.daylight && "opacity-50")}>
            {!draft.daylight && <p className="text-sm text-ink-2">Off: these apply when the switch above is on.</p>}
            <div className="flex flex-wrap gap-4">
              <div>
                <Label
                  htmlFor={latitudeId}
                  hintId={latitudeHintId}
                  hint="How far north (positive) or south (negative) of the equator the camera is, in decimal degrees, like 40.71. In a maps app, press on the spot and copy the two numbers; two decimals (about a kilometre) is plenty. They are used only to work out sunrise and sunset, and stay on this machine."
                >
                  Latitude
                </Label>
                <Input
                  id={latitudeId}
                  inputMode="decimal"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="40.71"
                  value={draft.latitude}
                  onChange={(event) => change({ latitude: event.target.value })}
                  aria-invalid={latitudeBad}
                  aria-describedby={latitudeHintId}
                  className={cn("w-36", latitudeBad && "border-critical")}
                />
              </div>
              <div>
                <Label
                  htmlFor={longitudeId}
                  hintId={longitudeHintId}
                  hint="How far east (positive) or west (negative) of Greenwich the camera is, like -74.01 for New York: a place in the Americas has a negative longitude. A wrong sign puts the sun's day in the middle of the night, and the box under the options warns when it sees that."
                >
                  Longitude
                </Label>
                <Input
                  id={longitudeId}
                  inputMode="decimal"
                  autoComplete="off"
                  spellCheck={false}
                  placeholder="-74.01"
                  value={draft.longitude}
                  onChange={(event) => change({ longitude: event.target.value })}
                  aria-invalid={longitudeBad}
                  aria-describedby={longitudeHintId}
                  className={cn("w-36", longitudeBad && "border-critical")}
                />
              </div>
            </div>
            <div className="flex flex-wrap gap-4">
              <div>
                <Label
                  htmlFor={beforeId}
                  hintId={beforeHintId}
                  hint={`How long before sunrise judging begins. 30 and 30 judge from half an hour before sunrise to half an hour after sunset. A negative number starts later than the sun does; from ${MIN_MARGIN} to ${MAX_MARGIN}.`}
                >
                  Start (minutes before sunrise)
                </Label>
                <Input
                  id={beforeId}
                  inputMode="numeric"
                  autoComplete="off"
                  value={draft.before}
                  onChange={(event) => change({ before: event.target.value })}
                  aria-invalid={beforeBad}
                  aria-describedby={beforeHintId}
                  className={cn("w-28", beforeBad && "border-critical")}
                />
              </div>
              <div>
                <Label
                  htmlFor={afterId}
                  hintId={afterHintId}
                  hint={`How long after sunset judging goes on. A negative number stops sooner than the sun sets; from ${MIN_MARGIN} to ${MAX_MARGIN}.`}
                >
                  End (minutes after sunset)
                </Label>
                <Input
                  id={afterId}
                  inputMode="numeric"
                  autoComplete="off"
                  value={draft.after}
                  onChange={(event) => change({ after: event.target.value })}
                  aria-invalid={afterBad}
                  aria-describedby={afterHintId}
                  className={cn("w-28", afterBad && "border-critical")}
                />
              </div>
            </div>
          </fieldset>
        </OptionGroup>

        <OptionGroup
          title="At set times"
          hint={
            <>
              One or more ranges of the day by the camera&rsquo;s clock ({state.status.timezone}), on the days you pick. A range may run through midnight, so 18:00 to 16:00 leaves out
              16:00 to 18:00.
            </>
          }
        >
          <Switch label="Judge stop signs only at set times" checked={draft.clock} onChange={(clock) => change({ clock })} />
          <fieldset disabled={!draft.clock} className={cn("grid gap-3 border-l-2 border-edge pl-4 transition-opacity", !draft.clock && "opacity-50")}>
            {!draft.clock && <p className="text-sm text-ink-2">Off: these apply when the switch above is on.</p>}
            <ul aria-label="Ranges of hours" className="grid gap-2">
              {draft.ranges.map((range, index) => (
                <li key={index} className="flex flex-wrap items-center gap-x-3 gap-y-1.5">
                  <span className="flex items-center gap-2">
                    <span className="text-sm text-ink-2">From</span>
                    <TimeField aria-label={`Range ${index + 1} from`} value={range.start} onChange={(start) => changeRange(index, { start })} />
                  </span>
                  <span className="flex items-center gap-2">
                    <span className="text-sm text-ink-2">to</span>
                    <TimeField aria-label={`Range ${index + 1} to`} value={range.end} onChange={(end) => changeRange(index, { end })} />
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      aria-label={`Remove range ${index + 1}`}
                      disabled={draft.ranges.length <= 1}
                      onClick={() => change({ ranges: removeRange(draft.ranges, index) })}
                    >
                      <X className="size-4" aria-hidden="true" />
                    </Button>
                  </span>
                  {range.start !== "" && range.end !== "" && range.end < range.start && <span className="basis-full text-sm text-ink-2">through midnight</span>}
                </li>
              ))}
            </ul>
            <div>
              <Button type="button" size="sm" disabled={draft.ranges.length >= MAX_RANGES} onClick={() => change({ ranges: addRange(draft.ranges) })}>
                Add another range
              </Button>
            </div>
            <OptionGroup
              title="On these days"
              className="gap-1.5"
              hint={draft.days.length === 0 ? "The days of the week the ranges apply on." : `Judged ${daysText(draft.days)}. A range through midnight belongs to the day it starts on.`}
            >
              <DayButtons days={draft.days} onChange={(days) => change({ days })} />
              {draft.days.length === 0 && <p className="text-sm text-critical">Pick at least one day.</p>}
            </OptionGroup>
          </fieldset>
        </OptionGroup>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button type="submit" variant="primary" busy={busy} disabled={!dirty || problem !== null || busy}>
          Save changes
        </Button>
        {problem !== null ? <StatusBadge level="critical">{problem}</StatusBadge> : dirty && <StatusBadge level="warning">Not saved yet</StatusBadge>}
        {outcome && (
          <p role={outcome.kind === "error" ? "alert" : "status"} className="text-sm text-ink">
            <span className={`mr-1.5 inline-block size-2 rounded-full align-middle ${outcome.kind === "error" ? "bg-critical" : "bg-good"}`} aria-hidden="true" />
            <span className="font-medium">{outcome.kind === "error" ? "Not saved. " : ""}</span>
            {outcome.text}
          </p>
        )}
      </div>
    </form>
  );
}
