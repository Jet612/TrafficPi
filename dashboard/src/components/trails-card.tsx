"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { CalendarDays, ChevronLeft, ChevronRight, Download, Pause, Play } from "lucide-react";
import { HourStrip } from "@/components/hour-strip";
import { TrailPicture } from "@/components/trail-picture";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { CheckChip } from "@/components/ui/check-chip";
import { ClassIcon } from "@/components/ui/class-icon";
import { InfoTip } from "@/components/ui/info-tip";
import { Segmented } from "@/components/ui/segmented";
import { ToggleChip } from "@/components/ui/toggle-chip";
import { useStats } from "@/components/stats-provider";
import { ApiError, api, referencePictureUrl, snapshotUrl } from "@/lib/api";
import { classLabel } from "@/lib/classes";
import { dayText, formatCount, yearIn } from "@/lib/format";
import { useNow, usePersistentState, useRequest } from "@/lib/hooks";
import {
  allDay,
  clampSpan,
  countIn,
  countText,
  dayTitle,
  evenSample,
  hourButtonLabel,
  hourCounts,
  isAllDay,
  isDay,
  kindTotals,
  kindsIn,
  nextHour,
  pathsOfKinds,
  pictureFileName,
  playlist,
  profileScene,
  shiftDay,
  shownKinds,
  spanBetween,
  spanLabel,
  spanRange,
  sameSpan,
  type HourSpan,
  type TrailView,
} from "@/lib/trails";
import { download, renderPicturePng } from "@/lib/trails-canvas";
import { heatGradientCss, type TrailScene } from "@/lib/trails-draw";
import type { TrailDay } from "@/lib/types";

/** How long each hour is shown while a day plays. */
const STEP_MS = 1600;
/** The newest day (and the paths of its hours) are fetched again this often. */
const REFRESH_MS = 60_000;
/** A run of quick changes (dragging across the hours) makes one request for the paths. */
const PATHS_DELAY_MS = 150;
const FULL_SCALE = 65535;
/** The Paths view draws at most this many; beyond that they are a smear, and the heat map is the way to see them. */
const MAX_PATHS_DRAWN = 600;

interface Prefs {
  view: TrailView;
  lines: boolean;
}
const DEFAULT_PREFS: Prefs = { view: "paths", lines: true };

export function TrailsCard() {
  const [stored, setPrefs] = usePersistentState<Partial<Prefs>>("trafficpi.trails", DEFAULT_PREFS);
  const prefs = useMemo<Prefs>(() => ({ ...DEFAULT_PREFS, ...stored }), [stored]);
  /** The day the visitor asked for; null while they have not, which means the newest day with trails. */
  const [chosenDay, setChosenDay] = useState<string | null>(null);
  /** The hours picked; null means the whole day, whatever its length. */
  const [picked, setPicked] = useState<HourSpan | null>(null);
  const [hidden, setHidden] = useState<ReadonlySet<string>>(() => new Set());
  const [playing, setPlaying] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [pictureFailures, setPictureFailures] = useState(0);
  const [nonce] = useState(() => Date.now());
  const resume = useRef<HourSpan | null>(null);
  const zone = useStats().today?.timezone ?? "UTC";
  const now = useNow(3_600_000);
  /** The year it is, which a day in another year is told with; until the clock is known, the day's own. */
  const thisYear = now === 0 ? null : yearIn(now, zone);

  // --- what there is to show ------------------------------------------------------------------

  const dayRequest = useRequest<TrailDay>(
    `day:${chosenDay ?? "latest"}`,
    async (signal) => {
      const first = await api.trailsDay(chosenDay ?? undefined, signal);
      // With no day chosen, an empty today (just after midnight, say) shows the last day that has trails.
      if (chosenDay === null && first.total === 0 && first.last_day !== null && first.last_day !== first.day) {
        return api.trailsDay(first.last_day, signal);
      }
      return first;
    },
    { refreshMs: REFRESH_MS },
  );
  const day = dayRequest.data;
  const dayLoading = dayRequest.loading;
  /** The day the picker shows: the one asked for, even while its answer is on the way. */
  const shownDay = chosenDay ?? day?.day ?? null;

  const kinds = useMemo(() => (day ? shownKinds(day, hidden) : new Set<string>()), [day, hidden]);
  const counts = useMemo(() => (day ? hourCounts(day, kinds) : []), [day, kinds]);
  const span = useMemo(() => (day ? (picked ? clampSpan(picked, day) : allDay(day)) : null), [day, picked]);
  const range = day && span ? spanRange(day, span) : null;
  const trailsInSpan = day && span ? countIn(day.hours.map((hour) => hour.count), span) : 0;
  const spanCount = span ? countIn(counts, span) : 0;

  const pathsKey = range && trailsInSpan > 0 && kinds.size > 0 ? `paths:${range.fromMs}:${range.toMs}` : null;
  const newest = day !== null && day.day === day.last_day;
  const pathsRequest = useRequest(
    pathsKey,
    (signal) => api.trails(range?.fromMs ?? 0, range?.toMs ?? 0, signal),
    { refreshMs: newest ? REFRESH_MS : undefined, delayMs: PATHS_DELAY_MS },
  );
  const pathsData = pathsKey === null ? null : pathsRequest.data;
  const visible = useMemo(() => (pathsData ? pathsOfKinds(pathsData.trails, kinds) : []), [pathsData, kinds]);
  // Paths are for seeing individual routes, and a few hundred is as many as can be told apart; the
  // heat map takes them all.
  const drawn = useMemo(() => (prefs.view === "paths" ? evenSample(visible, MAX_PATHS_DRAWN) : visible), [visible, prefs.view]);
  const pathsLoading = pathsKey !== null && pathsRequest.loading;

  const profileRequest = useRequest("profile", (signal) => api.profile(signal));
  const profileOut = profileRequest.data;
  const lineScene = useMemo(() => (profileOut ? profileScene(profileOut.profile) : { lines: [], zones: [], stopSigns: [] }), [profileOut]);

  // The picture the lines were drawn on if there is one, else what the camera sees; and, if a
  // picture cannot be loaded, the next choice, and in the end none.
  const pictureChoices: string[] = profileOut
    ? [...(profileOut.reference_at ? [referencePictureUrl(profileOut.reference_at)] : []), snapshotUrl(nonce)]
    : profileRequest.error !== null
      ? [snapshotUrl(nonce)]
      : [];
  const pictureUrl = pictureChoices[pictureFailures] ?? null;
  const pictureLoading = profileOut === null && profileRequest.error === null;

  const scene = useMemo<TrailScene>(
    () => ({
      paths: drawn,
      scale: pathsData?.scale ?? FULL_SCALE,
      view: prefs.view,
      lines: lineScene.lines,
      zones: lineScene.zones,
      stopSigns: lineScene.stopSigns,
      showLinesAndZones: prefs.lines,
    }),
    [drawn, pathsData, prefs.view, prefs.lines, lineScene],
  );

  // --- changing what is shown --------------------------------------------------------------------

  function goToDay(next: string | null) {
    setPlaying(false);
    setPicked(null);
    setChosenDay(next);
  }

  function choose(next: HourSpan | null) {
    setPlaying(false);
    setPicked((current) => {
      if (next === null) return null;
      return current !== null && sameSpan(current, next) ? current : next;
    });
  }

  function toggleKind(kind: string) {
    setHidden((current) => {
      const next = new Set(current);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  }

  function togglePlay() {
    if (playing) {
      setPlaying(false);
      return;
    }
    const list = playlist(counts);
    // From the first hour picked, or from the start of the day when the whole day is.
    const first = (day && span && !isAllDay(day, span) ? list.find((index) => index >= span.from) : undefined) ?? list[0];
    if (first === undefined) return;
    resume.current = picked;
    setPicked(spanBetween(first, first));
    setPlaying(true);
  }

  // Playing: after a moment on an hour, the next one that has something; at the end, back to what was picked.
  useEffect(() => {
    if (!playing || !span) return;
    const timer = setTimeout(() => {
      const next = nextHour(playlist(counts), span.from === span.to ? span.from : null);
      if (next === null) {
        setPlaying(false);
        setPicked(resume.current);
      } else {
        setPicked(spanBetween(next, next));
      }
    }, STEP_MS);
    return () => clearTimeout(timer);
  }, [playing, span, counts]);

  async function savePicture() {
    if (!day || !span) return;
    setSaving(true);
    setSaveError(null);
    try {
      const blob = await renderPicturePng({ pictureUrl, scene, caption: `${dayTitle(day.day, thisYear ?? Number(day.day.slice(0, 4)))} · ${spanLabel(day, span)} · ${summary}` });
      download(blob, pictureFileName(day, span, prefs.view));
    } catch (error) {
      setSaveError(error instanceof Error ? error.message : "Could not save the picture.");
    } finally {
      setSaving(false);
    }
  }

  // --- words ---------------------------------------------------------------------------------------

  // The number comes from the hours' own counts, which are known at once; the paths on screen may
  // still be the last hours' while these are fetched, and must not give this headline their count.
  const pathsCurrent = pathsKey !== null && pathsRequest.answeredKey === pathsKey;
  const summary = countText(spanCount, pathsCurrent ? drawn.length : spanCount, kinds, formatCount);
  const headline = day && span ? `${summary} · ${dayTitle(day.day, thisYear ?? Number(day.day.slice(0, 4)))} · ${spanLabel(day, span)}` : "";
  const description = day && span ? `${prefs.view === "heat" ? "Heat map" : "Paths"} of ${summary} on ${dayTitle(day.day, thisYear ?? Number(day.day.slice(0, 4)))}, ${spanLabel(day, span).toLowerCase()}, drawn on the camera picture.` : "";
  const emptyMessage = !day
    ? null
    : day.total === 0
      ? "No vehicles were recorded on this day."
      : kinds.size === 0
        ? "Every kind is switched off."
        : trailsInSpan === 0
          ? "No vehicles were recorded in these hours."
          : spanCount === 0
            ? "None of the kinds that are switched on were recorded in these hours."
            : null;

  // --- the card ---------------------------------------------------------------------------------------

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Trails</CardTitle>
          <CardSubtitle data-testid="trails-headline">{day && day.first_day !== null ? headline : "The path every vehicle took"}</CardSubtitle>
        </div>
        {day && day.first_day !== null && (
          <div className="flex flex-wrap items-center gap-2">
            <Segmented
              label="Draw as"
              value={prefs.view}
              options={[
                { value: "paths", label: "Paths" },
                { value: "heat", label: "Heat map" },
              ]}
              onChange={(view) => setPrefs({ ...prefs, view })}
            />
            <Button size="sm" variant="secondary" busy={saving} onClick={() => void savePicture()}>
              <Download className="size-4" aria-hidden="true" />
              Save picture
            </Button>
          </div>
        )}
      </CardHeader>
      <CardBody>
        {day === null ? (
          <div className="grid h-[260px] place-items-center text-center text-sm text-ink-2">
            {dayRequest.error !== null ? (
              <div>
                <p>Could not load the trails: {dayRequest.error instanceof ApiError ? dayRequest.error.message : "something went wrong"}.</p>
                <Button className="mt-3" size="sm" onClick={dayRequest.refresh}>
                  Try again
                </Button>
              </div>
            ) : (
              "Loading…"
            )}
          </div>
        ) : day.first_day === null ? (
          <p className="max-w-2xl py-8 text-sm text-ink-2">
            No trails have been recorded yet. A trail is saved when a vehicle that moves has left the camera&rsquo;s picture, so they appear once traffic
            has passed. If this stays empty, check that trails are switched on under Setup.
          </p>
        ) : (
          <div className="grid gap-3">
            {/* One toolbar: which day, which kinds, what is drawn over the picture, and the play button. */}
            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <div className="flex flex-wrap items-center gap-2">
                <div className="flex items-center gap-1.5">
                  <Button
                    size="sm"
                    variant="secondary"
                    aria-label="Previous day"
                    disabled={shownDay === null || shownDay <= day.first_day || shiftDay(shownDay, -1) === null}
                    onClick={() => shownDay !== null && goToDay(shiftDay(shownDay, -1))}
                  >
                    <ChevronLeft className="size-4" aria-hidden="true" />
                  </Button>
                  <DayPicker
                    value={shownDay ?? day.day}
                    min={day.first_day}
                    max={day.last_day ?? undefined}
                    thisYear={thisYear}
                    onChange={(value) => goToDay(value)}
                  />
                  <Button
                    size="sm"
                    variant="secondary"
                    aria-label="Next day"
                    disabled={shownDay === null || day.last_day === null || shownDay >= day.last_day || shiftDay(shownDay, 1) === null}
                    onClick={() => shownDay !== null && goToDay(shiftDay(shownDay, 1))}
                  >
                    <ChevronRight className="size-4" aria-hidden="true" />
                  </Button>
                  {chosenDay !== null && (
                    <Button size="sm" variant="ghost" onClick={() => goToDay(null)}>
                      Latest day
                    </Button>
                  )}
                </div>
                <KindFilter day={day} span={span ?? allDay(day)} hidden={hidden} onToggle={toggleKind} />
              </div>
              <div className="flex flex-wrap items-center gap-2">
                <ToggleChip checked={prefs.lines} onChange={(lines) => setPrefs({ ...prefs, lines })}>
                  Lines and zones
                </ToggleChip>
                <Button size="sm" variant="secondary" disabled={playlist(counts).length === 0} onClick={togglePlay} aria-pressed={playing}>
                  {playing ? <Pause className="size-4" aria-hidden="true" /> : <Play className="size-4" aria-hidden="true" />}
                  {playing ? "Pause" : "Play the day"}
                </Button>
              </div>
            </div>

            {span && (
              <div className="grid gap-1.5">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="inline-flex items-center gap-1 text-sm font-medium text-ink">
                    Hours
                    <InfoTip id="trail-hours-help">Pick an hour, drag across several, or Shift-click to extend.</InfoTip>
                  </p>
                  <Button size="sm" variant="secondary" aria-pressed={isAllDay(day, span)} className={isAllDay(day, span) ? "border-accent bg-accent-wash" : undefined} onClick={() => choose(null)}>
                    All day
                  </Button>
                </div>
                <HourStrip
                  hours={day.hours}
                  counts={counts}
                  span={span}
                  labelFor={(index) => hourButtonLabel(day, index, counts[index] ?? 0, kinds)}
                  onSelect={choose}
                  describedBy="trail-hours-help"
                />
              </div>
            )}

            <div aria-busy={dayLoading || pathsLoading} data-testid="trail-view">
              <TrailPicture
                pictureUrl={pictureUrl}
                noPictureText={pictureLoading ? "Loading the picture…" : "The camera's picture is not available. The paths are drawn on a plain background."}
                onPictureError={() => setPictureFailures((n) => n + 1)}
                scene={scene}
                description={description}
                dimmed={dayLoading || pathsLoading}
              >
                {emptyMessage && (
                  <p
                    role="status"
                    className="absolute inset-x-3 bottom-3 mx-auto w-fit max-w-full rounded-md bg-black/75 px-3 py-1.5 text-center text-sm text-white"
                  >
                    {emptyMessage}
                  </p>
                )}
              </TrailPicture>
              {prefs.view === "heat" && <HeatLegend />}
              {saveError && (
                <p role="alert" className="mt-2 text-sm text-critical">
                  {saveError}
                </p>
              )}
            </div>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

/** Which kinds of vehicle to show, each with how many there were in the hours picked. */
function KindFilter({
  day,
  span,
  hidden,
  onToggle,
}: {
  day: TrailDay;
  span: HourSpan;
  hidden: ReadonlySet<string>;
  onToggle: (kind: string) => void;
}) {
  const totals = kindTotals(day, span);
  const kinds = kindsIn(day);
  if (kinds.length === 0) return null;
  return (
    <fieldset className="flex flex-wrap items-center gap-2">
      <legend className="sr-only">Kinds to show</legend>
      {kinds.map((kind) => (
        <CheckChip key={kind} checked={!hidden.has(kind)} onChange={() => onToggle(kind)}>
          <ClassIcon name={kind} />
          {classLabel(kind)}
          <span className="tabular-nums text-ink-3">{formatCount(totals.get(kind) ?? 0)}</span>
        </CheckChip>
      ))}
    </fieldset>
  );
}

/**
 * The day, said the way every other day on the page is ("Fri, Oct 9"). The browser's own date box is
 * laid invisibly over it and opens its calendar, because that box writes the date in the browser's
 * language ("10/09/2026"); it is still the control for a screen reader and the keyboard.
 */
function DayPicker({ value, min, max, thisYear, onChange }: { value: string; min: string; max?: string; thisYear: number | null; onChange: (day: string) => void }) {
  return (
    <label className="relative inline-flex h-8 items-center gap-2 rounded-lg border border-baseline bg-surface px-3 text-sm text-ink pointer-coarse:h-10 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent">
      <CalendarDays className="size-4 text-ink-3" aria-hidden="true" />
      <span className="tabular-nums">{dayText(value, thisYear ?? Number(value.slice(0, 4)))}</span>
      <input
        type="date"
        aria-label="Day"
        value={value}
        min={min}
        max={max}
        onChange={(event) => {
          if (isDay(event.target.value)) onChange(event.target.value);
        }}
        onClick={(event) => {
          try {
            event.currentTarget.showPicker();
          } catch {
            // not every browser can open it from a click: the box is still there to type in
          }
        }}
        className="absolute inset-0 size-full cursor-pointer opacity-0"
      />
    </label>
  );
}

/** The ramp of the heat map, from few vehicles to many. */
function HeatLegend() {
  return (
    <div className="mt-2 flex items-center gap-2 text-sm text-ink-2" aria-hidden="true">
      <span>Fewer vehicles</span>
      <span className="h-2.5 w-40 rounded-full border border-edge" style={{ background: heatGradientCss() }} />
      <span>More</span>
    </div>
  );
}
