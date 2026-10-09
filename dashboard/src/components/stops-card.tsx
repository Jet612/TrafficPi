"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import { StatusBadge } from "@/components/ui/status";
import { useStats } from "@/components/stats-provider";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { classColor, classSingular } from "@/lib/classes";
import { formatCount, formatDateTimeSeconds } from "@/lib/format";
import { useNow, usePersistentState, useRequest } from "@/lib/hooks";
import {
  NEEDS_A_LOOK,
  OUTCOMES,
  OUTCOME_LABEL,
  OUTCOME_LEVEL,
  PERIOD_OPTIONS,
  REVIEW_LABEL,
  counted,
  nextReview,
  numbersText,
  periodRange,
  signLabel,
  summarySentence,
  toggleOutcome,
  type Period,
} from "@/lib/stops";
import type { ReviewState, StopOut, StopOutcome } from "@/lib/types";

const PAGE = 10;
const REFRESH_MS = 15_000;

type ReviewFilter = "all" | "unreviewed" | ReviewState;
const REVIEW_FILTERS: readonly { value: ReviewFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "unreviewed", label: "Not looked at" },
  { value: "confirmed", label: "Confirmed" },
  { value: "false_positive", label: "False alarms" },
];

export function StopsCard() {
  const [stored, setStored] = usePersistentState<{ period?: Period }>("trafficpi.stops", {});
  const period: Period = stored.period ?? "day";
  const [selected, setSelected] = useState<readonly StopOutcome[]>(NEEDS_A_LOOK);
  const [review, setReview] = useState<ReviewFilter>("all");
  const [sign, setSign] = useState<string>("all");
  const [limit, setLimit] = useState(PAGE);
  const [answers, setAnswers] = useState<ReadonlyMap<number, StopOut>>(() => new Map());
  const [reviewing, setReviewing] = useState<ReadonlySet<number>>(() => new Set());
  const [problem, setProblem] = useState<string | null>(null);
  const zone = useStats().today?.timezone ?? "UTC";
  const now = useNow(60_000); // 0 until the browser has the time: nothing is asked before that
  const { fromMs, toMs } = useMemo(() => periodRange(period, now), [period, now]);

  const profile = useRequest("profile", (signal) => api.profile(signal));
  const signs = profile.data?.profile.stop_signs ?? [];

  const summary = useRequest(now === 0 ? null : `stops-summary:${fromMs}:${toMs}`, (signal) => api.stopSummary(fromMs, toMs, signal), {
    refreshMs: REFRESH_MS,
  });
  const query = {
    fromMs,
    toMs,
    outcomes: selected,
    review: review === "all" ? undefined : review,
    sign: sign === "all" ? undefined : sign,
    limit,
  };
  const listKey = now === 0 || selected.length === 0 ? null : `stops:${JSON.stringify(query)}`;
  const list = useRequest(listKey, (signal) => api.stops(query, signal), { refreshMs: REFRESH_MS });
  const rows = useMemo(
    () => (listKey === null || list.data === null ? [] : list.data).map((row) => answers.get(row.id) ?? row),
    [listKey, list.data, answers],
  );

  async function make(row: StopOut, pressed: ReviewState) {
    setProblem(null);
    setReviewing((current) => new Set(current).add(row.id));
    try {
      const updated = await api.reviewStop(row.id, nextReview(row.review, pressed));
      setAnswers((current) => new Map(current).set(updated.id, updated));
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

  const everything = summary.data?.outcomes ?? [];
  const noSigns = profile.data !== null && signs.length === 0;
  const nothingEver = noSigns && everything.every((totals) => totals.total === 0);

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Stop signs</CardTitle>
          <p className="mt-0.5 text-sm text-ink-2" data-testid="stops-headline">
            {summary.data ? summarySentence(summary.data) : summary.error !== null ? "The numbers could not be loaded." : "Loading…"}
          </p>
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
          <div className="grid gap-4">
            <ul aria-label="Outcomes" className="grid grid-cols-2 gap-2 md:grid-cols-4">
              {OUTCOMES.map((outcome) => {
                const totals = everything.find((candidate) => candidate.outcome === outcome);
                const pressed = selected.includes(outcome);
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
                        "flex h-full w-full flex-col rounded-lg border px-3 py-2 text-left transition-colors",
                        pressed ? "border-accent bg-accent-wash text-ink" : "border-edge bg-surface text-ink-2 hover:bg-sunken",
                      )}
                    >
                      <span className="text-sm">{OUTCOME_LABEL[outcome]}</span>
                      <span className="text-2xl font-semibold leading-tight tabular-nums text-ink" data-testid={`outcome-${outcome}`}>
                        {totals ? formatCount(counted(totals)) : "–"}
                      </span>
                      <span className="text-xs text-ink-3">{totals && totals.unreviewed > 0 ? `${formatCount(totals.unreviewed)} not looked at` : " "}</span>
                    </button>
                  </li>
                );
              })}
            </ul>

            <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
              <p className="text-sm text-ink-2">
                {selected.length === 0
                  ? "Pick at least one of the outcomes above to see the passes."
                  : "Showing the outcomes that are pressed, newest first."}
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <Segmented label="Show" value={review} options={REVIEW_FILTERS} onChange={(value) => { setReview(value); setLimit(PAGE); }} />
                {signs.length > 1 && (
                  <select
                    aria-label="Stop sign"
                    value={sign}
                    onChange={(event) => {
                      setSign(event.target.value);
                      setLimit(PAGE);
                    }}
                    className="h-8 rounded-lg border border-baseline bg-surface px-2 text-sm text-ink"
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
                  : selected.length === OUTCOMES.length || NEEDS_A_LOOK.every((outcome) => selected.includes(outcome))
                    ? "Nothing needed a look in this time."
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
                      sign={signLabel(profile.data?.profile ?? null, row.sign)}
                      busy={reviewing.has(row.id)}
                      onReview={(pressed) => void make(row, pressed)}
                    />
                  ))}
                </ol>
                {rows.length >= limit && (
                  <div className="mt-3">
                    <Button size="sm" onClick={() => setLimit((current) => current + PAGE)}>
                      Show more
                    </Button>
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function StopRow({
  row,
  zone,
  sign,
  busy,
  onReview,
}: {
  row: StopOut;
  zone: string;
  sign: string;
  busy: boolean;
  onReview: (pressed: ReviewState) => void;
}) {
  const kind = row.label ?? "";
  const when = formatDateTimeSeconds(row.t, zone);
  const what = `${classSingular(kind).toLowerCase()} at ${sign}, ${when}`;
  return (
    <li className="grid gap-x-4 gap-y-1.5 border-t border-grid py-3 text-sm first:border-t-0 first:pt-0 md:grid-cols-[minmax(0,1fr)_auto] md:items-center" data-outcome={row.outcome} data-review={row.review ?? "none"}>
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <time className="tabular-nums text-ink-2" dateTime={new Date(row.t).toISOString()}>
            {when}
          </time>
          <span className="inline-flex items-center gap-1.5 text-ink">
            <span className="h-[3px] w-3 shrink-0 rounded-full" style={{ background: classColor(kind) }} aria-hidden="true" />
            {classSingular(kind)}
          </span>
          <span className="truncate text-ink-2">{sign}</span>
          <StatusBadge level={OUTCOME_LEVEL[row.outcome]}>{OUTCOME_LABEL[row.outcome]}</StatusBadge>
        </p>
        <p className="mt-1 text-ink-3">{numbersText(row)}</p>
      </div>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={`Your say on the ${what}`}>
        {(["confirmed", "false_positive"] as const).map((state) => (
          <Button
            key={state}
            size="sm"
            variant="secondary"
            disabled={busy}
            aria-pressed={row.review === state}
            aria-label={`${state === "confirmed" ? "Confirm" : "False alarm"}: ${what}`}
            onClick={() => onReview(state)}
            className={row.review === state ? "bg-accent-wash" : undefined}
          >
            {state === "confirmed" ? <Check className="size-4" aria-hidden="true" /> : <X className="size-4" aria-hidden="true" />}
            {state === "confirmed" ? "Confirm" : "False alarm"}
          </Button>
        ))}
        {row.review && <span className="sr-only">{REVIEW_LABEL[row.review]}</span>}
      </div>
    </li>
  );
}
