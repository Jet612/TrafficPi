"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Check, ChevronRight, ImageOff, Play } from "lucide-react";
import { ReviewButtons } from "@/components/review-buttons";
import { StopHoursNote } from "@/components/stop-hours-note";
import { StopViewer } from "@/components/stop-viewer";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { ClassIcon } from "@/components/ui/class-icon";
import { InfoTip } from "@/components/ui/info-tip";
import { OUTCOME_ICON } from "@/components/ui/outcome-icon";
import { METRIC_SIZE } from "@/components/ui/stat";
import { Segmented } from "@/components/ui/segmented";
import { StatusBadge } from "@/components/ui/status";
import { useStats } from "@/components/stats-provider";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { classSingular } from "@/lib/classes";
import { OUTCOME_COLOR } from "@/lib/data";
import { formatCount, fullTimeText, momentText } from "@/lib/format";
import { useNow, usePersistentState, useRequest } from "@/lib/hooks";
import { MEASURED_CAVEAT } from "@/lib/speed";
import {
  NEEDS_REVIEW,
  OUTCOMES,
  OUTCOME_LABEL,
  OUTCOME_LEVEL,
  PERIOD_OPTIONS,
  REVIEW_FILTERS,
  counted,
  isDefaultOutcomes,
  listTotal,
  listedPasses,
  nextReview,
  numbersText,
  passText,
  passesOf,
  periodRange,
  reviewProgress,
  reviewQuery,
  signLabel,
  summarySentence,
  toggleOutcome,
  unkeptEvidence,
  type Period,
  type ReviewFilter,
} from "@/lib/stops";
import type { ReviewState, StopOut, StopOutcome } from "@/lib/types";

const PAGE = 10;
const REFRESH_MS = 15_000;
const EMPTY: ReadonlySet<number> = new Set();

export function StopsCard() {
  const [stored, setStored] = usePersistentState<{ period?: Period }>("trafficpi.stops", {});
  const period: Period = stored.period ?? "day";
  const [selected, setSelected] = useState<readonly StopOutcome[]>(NEEDS_REVIEW);
  const [review, setReview] = useState<ReviewFilter>("all");
  const [sign, setSign] = useState<string>("all");
  const [limit, setLimit] = useState(PAGE);
  const [answers, setAnswers] = useState<ReadonlyMap<number, StopOut>>(() => new Map());
  /** The passes reviewed in the view named by `view`: they stay in the list until the view changes. */
  const [kept, setKept] = useState<{ view: string; ids: ReadonlySet<number> }>({ view: "", ids: new Set() });
  const [reviewing, setReviewing] = useState<ReadonlySet<number>>(() => new Set());
  const [problem, setProblem] = useState<string | null>(null);
  const [viewing, setViewing] = useState<number | null>(null);
  const zone = useStats().today?.timezone ?? "UTC";
  const now = useNow(60_000); // 0 until the browser has the time: nothing is asked before that
  const { fromMs, toMs } = useMemo(() => periodRange(period, now), [period, now]);

  const profile = useRequest("profile", (signal) => api.profile(signal));
  const signs = profile.data?.profile.stop_signs ?? [];
  const settings = useRequest("settings", (signal) => api.settings(signal));
  const units = settings.data?.units ?? "mph";

  const summary = useRequest(now === 0 ? null : `stops-summary:${fromMs}:${toMs}`, (signal) => api.stopSummary(fromMs, toMs, signal), {
    refreshMs: REFRESH_MS,
  });
  const query = {
    fromMs,
    toMs,
    outcomes: selected,
    review: reviewQuery(review),
    sign: sign === "all" ? undefined : sign,
    limit,
  };
  const listKey = now === 0 || selected.length === 0 ? null : `stops:${JSON.stringify(query)}`;
  const list = useRequest(listKey, (signal) => api.stops(query, signal), { refreshMs: REFRESH_MS });
  // What is listed is chosen by these; the page of rows and the time window move on without changing the view.
  const view = `${period}|${selected.join(",")}|${review}|${sign}`;
  const keptHere = kept.view === view ? kept.ids : EMPTY;
  const rows = useMemo(
    () => (listKey === null || list.data === null ? [] : listedPasses(list.data, answers, keptHere, limit)),
    [listKey, list.data, answers, keptHere, limit],
  );

  async function make(row: StopOut, pressed: ReviewState) {
    setProblem(null);
    setReviewing((current) => new Set(current).add(row.id));
    try {
      const updated = await api.reviewStop(row.id, nextReview(row.review, pressed));
      setAnswers((current) => new Map(current).set(updated.id, updated));
      setKept((current) => ({ view, ids: new Set(current.view === view ? current.ids : EMPTY).add(updated.id) }));
      summary.refresh();
      list.refresh();
    } catch (error) {
      setProblem(error instanceof ApiError ? error.message : "Could not save that.");
    } finally {
      setReviewing((current) => {
        const next = new Set(current);
        next.delete(row.id);
        return next;
      });
    }
  }

  const viewed = viewing === null ? -1 : rows.findIndex((row) => row.id === viewing);
  const viewedRow = rows[viewed] ?? null; // null too when the pass has left the list since it was opened
  const everything = summary.data?.outcomes ?? [];
  const noSigns = profile.data !== null && signs.length === 0;
  const nothingEver = noSigns && everything.every((totals) => totals.total === 0);
  const total = summary.data ? listTotal(summary.data, selected, review, sign !== "all") : null;
  const changed = !isDefaultOutcomes(selected) || review !== "all" || sign !== "all";
  const unkept = settings.data ? unkeptEvidence(settings.data, selected) : [];
  const anyPicture = rows.some((row) => row.snapshot_url !== null || row.clip_url !== null);

  function reset() {
    setSelected(NEEDS_REVIEW);
    setReview("all");
    setSign("all");
    setLimit(PAGE);
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Stop signs</CardTitle>
          <CardSubtitle data-testid="stops-headline">
            {summary.data ? summarySentence(summary.data) : summary.error !== null ? "The numbers could not be loaded." : "Loading…"}
          </CardSubtitle>
        </div>
        <Segmented
          label="Period"
          value={period}
          options={PERIOD_OPTIONS.map(({ value, label }) => ({ value, label }))}
          onChange={(value) => {
            setStored({ ...stored, period: value });
            setLimit(PAGE);
          }}
        />
      </CardHeader>
      <CardBody>
        {nothingEver ? (
          <p className="max-w-2xl py-6 text-sm text-ink-2">
            No stop sign is set up yet.{" "}
            <Link href="/setup/" className="font-medium text-accent underline underline-offset-2">
              Add one on the Setup page
            </Link>{" "}
            to see whether vehicles stop before its line.
          </p>
        ) : (
          <div className="grid grid-cols-1 gap-4">
            <StopHoursNote />
            {/* The tiles are the one control for which outcomes to list: each can be pressed on or off. */}
            <ul aria-label="Outcomes" className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {OUTCOMES.map((outcome) => {
                const totals = everything.find((candidate) => candidate.outcome === outcome);
                const pressed = selected.includes(outcome);
                const progress = totals && outcome !== "stopped" ? reviewProgress(totals) : null;
                const Icon = OUTCOME_ICON[outcome];
                return (
                  <li key={outcome}>
                    <button
                      type="button"
                      aria-pressed={pressed}
                      onClick={() => {
                        setSelected(toggleOutcome(selected, outcome));
                        setLimit(PAGE);
                      }}
                      className={cn(
                        "flex h-full w-full flex-col rounded-lg border px-3 py-2.5 text-left transition-colors",
                        pressed ? "border-accent bg-accent-wash text-ink" : "border-edge bg-surface text-ink-2 hover:bg-sunken",
                      )}
                    >
                      <span className="flex items-center gap-1.5 text-sm">
                        <Icon className="size-4 shrink-0" style={{ color: OUTCOME_COLOR[outcome] }} aria-hidden="true" />
                        {OUTCOME_LABEL[outcome]}
                        <span className="ml-auto grid size-4 place-items-center" aria-hidden="true">
                          {pressed && <Check className="size-4 text-accent" />}
                        </span>
                      </span>
                      <span className={cn(METRIC_SIZE.tile, "mt-1 tabular-nums")} data-testid={`outcome-${outcome}`}>
                        {totals ? formatCount(counted(totals)) : "–"}
                      </span>
                      {/* Said only once a review has begun: before that it would repeat the number above it on every tile. */}
                      {progress !== null && progress.reviewed > 0 && (
                        <span className="mt-2 block">
                          <span className="block h-1 w-full overflow-hidden rounded-full bg-sunken" aria-hidden="true">
                            <span className="block h-full rounded-full bg-accent" style={{ width: `${Math.round(progress.fraction * 100)}%` }} />
                          </span>
                          <span className="mt-1 block text-xs text-ink-3">{progress.waiting === 0 ? "All reviewed" : `${formatCount(progress.waiting)} need review`}</span>
                        </span>
                      )}
                    </button>
                  </li>
                );
              })}
            </ul>

            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <p className="flex flex-wrap items-center gap-x-1.5 text-sm text-ink-2" data-testid="stops-showing">
                {selected.length === 0 ? (
                  "Pick at least one of the outcomes above to see the passes."
                ) : (
                  <>
                    {total === null ? "Newest first" : `Showing ${formatCount(total)} ${total === 1 ? "pass" : "passes"} · newest first`}
                    <InfoTip>A pass is one vehicle reaching a stop sign. All leaves out the passes called false alarms; they are under False alarms.</InfoTip>
                  </>
                )}
                {changed && (
                  <>
                    <span aria-hidden="true">·</span>
                    <button type="button" onClick={reset} className="font-medium text-accent underline underline-offset-2">
                      Reset
                    </button>
                  </>
                )}
              </p>
              <div className="flex min-w-0 max-w-full flex-wrap items-center gap-3">
                <Segmented
                  label="Review status"
                  value={review}
                  options={REVIEW_FILTERS}
                  onChange={(value) => {
                    setReview(value);
                    setLimit(PAGE);
                  }}
                />
                {signs.length > 1 && (
                  <select
                    aria-label="Stop sign"
                    value={sign}
                    onChange={(event) => {
                      setSign(event.target.value);
                      setLimit(PAGE);
                    }}
                    className="h-8 rounded-lg border border-baseline bg-surface px-2 text-sm text-ink pointer-coarse:h-10"
                  >
                    <option value="all">All stop signs</option>
                    {signs.map((one) => (
                      <option key={one.id} value={one.id}>
                        {signLabel(profile.data?.profile ?? null, one.id)}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>

            {problem && (
              <p role="alert" className="text-sm text-critical">
                {problem}
              </p>
            )}

            {rows.length > 0 && unkept.length > 0 && (
              <p className="flex items-start gap-2 text-sm text-ink-3" data-testid="stops-no-clips">
                <ImageOff className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                <span>
                  No picture or clip is kept of {passesOf(unkept)} (switched off in Setup).{" "}
                  <Link href="/setup/" className="font-medium text-accent underline underline-offset-2">
                    Change in Setup
                  </Link>
                </span>
              </p>
            )}

            {listKey === null ? null : list.error !== null && list.data === null ? (
              <p className="text-sm text-ink-2">
                The passes could not be loaded.{" "}
                <Button size="sm" variant="ghost" onClick={list.refresh}>
                  Try again
                </Button>
              </p>
            ) : list.data === null ? (
              <p className="text-sm text-ink-2">Loading…</p>
            ) : rows.length === 0 ? (
              <p className="py-4 text-sm text-ink-2" data-testid="stops-empty">
                {review !== "all" || sign !== "all"
                  ? "Nothing matches that."
                  : selected.length === OUTCOMES.length || isDefaultOutcomes(selected)
                    ? "Nothing needs review in this time."
                    : "None of those in this time."}
              </p>
            ) : (
              <div>
                <ol aria-label="Passes at stop signs" className="flex flex-col" aria-busy={list.loading}>
                  {rows.map((row) => (
                    <StopRow
                      key={row.id}
                      row={row}
                      zone={zone}
                      now={now}
                      units={units}
                      sign={signLabel(profile.data?.profile ?? null, row.sign)}
                      showSign={signs.length > 1}
                      pictureSlot={anyPicture}
                      busy={reviewing.has(row.id)}
                      onReview={(pressed) => void make(row, pressed)}
                      onOpen={() => setViewing(row.id)}
                    />
                  ))}
                </ol>
                <div className="mt-3 flex flex-wrap items-center gap-3 text-sm text-ink-2">
                  <span>{total === null ? `Showing ${rows.length}` : `Showing ${rows.length} of ${formatCount(Math.max(total, rows.length))}`}</span>
                  {rows.length >= limit && (
                    <Button size="sm" onClick={() => setLimit((current) => current + PAGE)}>
                      Show more
                    </Button>
                  )}
                </div>
                {rows.some((row) => row.calibrated) && (
                  <p className="mt-2 max-w-3xl text-sm text-ink-3" data-testid="stops-measured-caveat">
                    {MEASURED_CAVEAT}
                  </p>
                )}
              </div>
            )}
          </div>
        )}
      </CardBody>
      {viewedRow !== null && (
        <StopViewer
          row={viewedRow}
          sign={signLabel(profile.data?.profile ?? null, viewedRow.sign)}
          zone={zone}
          units={units}
          busy={reviewing.has(viewedRow.id)}
          newer={rows[viewed - 1] ?? null}
          older={rows[viewed + 1] ?? null}
          onShow={(row) => setViewing(row.id)}
          onReview={(pressed) => void make(viewedRow, pressed)}
          onClose={() => setViewing(null)}
        />
      )}
    </Card>
  );
}

/**
 * One pass, on a line: when, what kind, what became of it, the numbers behind that, and the two buttons
 * of a review. The line opens the pass (its picture and clip, if there are any, and the same buttons at
 * full size). Only a pass with a picture has a picture in its row.
 */
function StopRow({
  row,
  zone,
  now,
  units,
  sign,
  showSign,
  pictureSlot,
  busy,
  onReview,
  onOpen,
}: {
  row: StopOut;
  zone: string;
  now: number;
  units: "mph" | "kmh";
  sign: string;
  showSign: boolean;
  pictureSlot: boolean;
  busy: boolean;
  onReview: (pressed: ReviewState) => void;
  onOpen: () => void;
}) {
  const kind = row.label ?? "";
  const what = passText(row, sign, zone);
  const hasPicture = row.snapshot_url !== null || row.clip_url !== null;
  return (
    <li
      className="flex items-center gap-2 border-t border-grid py-1.5 text-sm first:border-t-0 first:pt-0"
      data-outcome={row.outcome}
      data-review={row.review ?? "none"}
    >
      <button
        type="button"
        onClick={onOpen}
        aria-label={`${row.clip_url !== null ? "Watch the clip of" : hasPicture ? "Look at the picture of" : "Open"} the ${what}`}
        className="group -ml-2 flex min-w-0 flex-1 items-center gap-3 rounded-lg px-2 py-1 text-left transition-colors hover:bg-sunken"
      >
        {pictureSlot && (
          <span className="relative block h-9 w-16 shrink-0 overflow-hidden rounded-md bg-sunken">
            {row.snapshot_url !== null && (
              // eslint-disable-next-line @next/next/no-img-element -- a picture from the API, kept as it is
              <img src={row.snapshot_url} alt="" loading="lazy" decoding="async" className="size-full object-cover" />
            )}
            {row.clip_url !== null && (
              <span className="absolute inset-0 grid place-items-center bg-black/25 text-white">
                <Play className="size-4 fill-current" aria-hidden="true" />
              </span>
            )}
          </span>
        )}
        <span className="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-1 sm:gap-x-3">
          <time className="tabular-nums text-ink" dateTime={new Date(row.t).toISOString()} title={fullTimeText(row.t, zone)}>
            {momentText(row.t, zone, now)}
          </time>
          <span className="inline-flex items-center gap-1.5 text-ink">
            <ClassIcon name={kind} />
            {classSingular(kind)}
          </span>
          <StatusBadge level={OUTCOME_LEVEL[row.outcome]}>{OUTCOME_LABEL[row.outcome]}</StatusBadge>
          {showSign && <span className="truncate text-ink-2">{sign}</span>}
          <span className="text-ink-3">{numbersText(row, units)}</span>
        </span>
        <ChevronRight className="hidden size-4 shrink-0 text-ink-3 transition-colors group-hover:text-ink sm:block" aria-hidden="true" />
      </button>
      <ReviewButtons compact review={row.review} busy={busy} what={what} onReview={onReview} className="shrink-0" />
    </li>
  );
}
