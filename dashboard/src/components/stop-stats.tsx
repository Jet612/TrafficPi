"use client";

import Link from "next/link";
import { ArrowDown, ArrowUp, Minus } from "lucide-react";
import { StackedChartCard } from "@/components/stacked-chart";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { HowItWorks } from "@/components/ui/how-it-works";
import { OUTCOME_ICON } from "@/components/ui/outcome-icon";
import { ShareBar } from "@/components/ui/share-bar";
import { METRIC_SIZE } from "@/components/ui/stat";
import { cn } from "@/lib/cn";
import { classLabel } from "@/lib/classes";
import {
  MIN_SAMPLE,
  OUTCOME_COLOR,
  OUTCOME_SERIES,
  SMALL_SAMPLE,
  changeInPoints,
  changeTone,
  comparedWith,
  outcomeDayRows,
  outcomeHourRows,
  peak,
  pointsText,
  rateExtremes,
  sharesOf,
  speedText,
  stopRate,
  stopRateText,
  totalOf,
  violationsOf,
  weekdayName,
  type DataPeriod,
  type OutcomeCounts,
  type Tone,
} from "@/lib/data";
import { ESTIMATE_CAVEAT, MEASURED_CAVEAT, fastestText, speedPhrase, speedShort } from "@/lib/speed";
import { formatCount, hourRange } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { OUTCOMES, OUTCOME_LABEL, signLabel } from "@/lib/stops";
import type { Profile, StopOutcome, StopStats } from "@/lib/types";


const plural = (n: number, one: string, many = `${one}s`) => `${formatCount(n)} ${n === 1 ? one : many}`;
const percentOf = (part: number, whole: number) => (whole === 0 ? 0 : Math.round((part / whole) * 100));

/** Everything about the stop signs: how drivers did, when, who, and how. */
export function StopStatsSection({ stats, period, profile, busy }: { stats: StopStats; period: DataPeriod; profile: Profile | null; busy: boolean }) {
  const days = stats.by_day.length;
  const kinds = stats.by_kind.map((row) => ({ ...row, key: row.label, name: classLabel(row.label) }));
  const signs = stats.by_sign.map((row) => ({ ...row, key: row.sign, name: signLabel(profile, row.sign) }));
  const weekdays = stats.by_weekday.map((row) => ({ ...row, key: String(row.weekday), name: weekdayName(row.weekday) }));
  const busiest = peak(stats.by_hour, totalOf);
  const fastestOn = new Map(stats.by_day.map((day) => [day.date, day.fastest]));
  // A day's fastest speed is either measured on the calibrated road or an estimate from the vehicle's
  // size, never a mixture. Once any day is measured the column is "Fastest", and an estimate among
  // them is marked with a ≈.
  const anyMeasured = stats.by_day.some((day) => day.fastest?.calibrated);
  return (
    <>
      <HowDriversDid stats={stats} period={period} busy={busy} />
      <Highlights stats={stats} />
      <StackedChartCard
        title="Stop-sign results by hour"
        subtitle={busiest ? `Busiest hour ${hourRange(busiest.item.hour)} · ${plural(busiest.value, "vehicle")}` : undefined}
        rows={outcomeHourRows(stats.by_hour)}
        series={OUTCOME_SERIES}
        unit="vehicle"
        icons={OUTCOME_ICON}
        shareToggle
        interval={2}
        emptyText="No vehicle was judged in this time"
        firstColumn="Hour"
        extraColumns={[{ header: "Stop rate", cell: (row) => stopRateText(row.values) }]}
        summary={`Vehicles judged at the stop signs, by the hour of the day over ${plural(days, "day")}, stacked by what they did.${busiest ? ` The busiest hour is ${hourRange(busiest.item.hour)} with ${busiest.value}.` : " None was judged."}`}
        busy={busy}
        testId="data-stops-by-hour"
      />
      {days > 1 && (
        <StackedChartCard
          title="Stop-sign results by day"
          subtitle="Today is the last column, and it is not over yet."
          startAtData
          maxBar={64}
          rows={outcomeDayRows(stats.by_day)}
          series={OUTCOME_SERIES}
          unit="vehicle"
          icons={OUTCOME_ICON}
          shareToggle
          interval={Math.max(0, Math.ceil(days / 10) - 1)}
          emptyText="No vehicle was judged in this time"
          firstColumn="Day"
          extraColumns={[
            { header: "Stop rate", cell: (row) => stopRateText(row.values) },
            {
              header: anyMeasured ? "Fastest" : "Fastest (est.)",
              cell: (row) => {
                const fastest = fastestOn.get(row.id);
                if (!fastest) return "–";
                const text = speedShort(fastest.speed_ms, stats.units, fastest.calibrated);
                return anyMeasured && !fastest.calibrated ? `≈ ${text}` : text;
              },
            },
          ]}
          summary={`Vehicles judged at the stop signs on each of ${days} days, stacked by what they did. The table also gives each day's fastest ${anyMeasured ? "speed; a day marked ≈ has only an estimate, not a measurement" : "estimated speed"}.`}
          busy={busy}
          testId="data-stops-by-day"
        />
      )}
      {/* The small cards, two to a row, with a last one that would be alone given the whole row: no half-empty rows. */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2 lg:[&>:last-child:nth-child(odd)]:col-span-2">
        <BreakdownTable title="By kind of vehicle" firstHeader="Kind" rows={kinds} testId="data-stops-by-kind" />
        <HowTheyStop stats={stats} />
        <Reviews stats={stats} />
        {signs.length > 1 && <BreakdownTable title="By stop sign" firstHeader="Stop sign" rows={signs} testId="data-stops-by-sign" />}
        {days >= 14 && <BreakdownTable title="By day of the week" firstHeader="Day" rows={weekdays} testId="data-stops-by-weekday" />}
      </div>
    </>
  );
}

// --- the headline numbers ------------------------------------------------------------------------

function HowDriversDid({ stats, period, busy }: { stats: StopStats; period: DataPeriod; busy: boolean }) {
  const total = totalOf(stats.totals);
  const before = comparedWith(period);
  const rate = stopRate(stats.totals);
  const falseAlarms = stats.review.false_alarms;
  const nothingBefore = before !== null && stats.previous !== null && totalOf(stats.previous) === 0;
  return (
    <Card aria-busy={busy}>
      <CardHeader>
        <div>
          <CardTitle as="h3">Stop-sign results</CardTitle>
          <CardSubtitle data-testid="data-stops-headline">
            {total === 0 ? "No vehicle has reached a stop sign in this time." : `${plural(total, "vehicle")} reached a stop sign.`}
          </CardSubtitle>
        </div>
      </CardHeader>
      <CardBody className={cn("grid gap-4 transition-opacity", busy && "opacity-60")}>
        <ul aria-label="Share of the vehicles by what they did" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
          {OUTCOMES.map((outcome) => (
            <OutcomeTile key={outcome} outcome={outcome} counts={stats.totals} before={stats.previous} beforeText={before} />
          ))}
        </ul>
        <ShareBar counts={stats.totals} />
        <ul className="grid gap-1 text-sm text-ink-2">
          {total > 0 && total < SMALL_SAMPLE && <li>Only {plural(total, "vehicle")} so far, so these percentages will move a lot as more are judged.</li>}
          {rate !== null && stats.totals.indeterminate > 0 && (
            <li data-testid="data-stops-rate">
              Of the {plural(rate.of, "vehicle")} whose pass could be judged, {rate.percent}% stopped.
            </li>
          )}
          {falseAlarms > 0 && <li>{plural(falseAlarms, "vehicle")} you called false alarms {falseAlarms === 1 ? "is" : "are"} left out.</li>}
          {nothingBefore && total > 0 && <li>No earlier period to compare with.</li>}
        </ul>
      </CardBody>
    </Card>
  );
}

/** One outcome: its icon and name, its share as the number the tile is about, how many of how many, and how it moved. All four are the same size: no outcome is the hero. */
function OutcomeTile({ outcome, counts, before, beforeText }: { outcome: StopOutcome; counts: OutcomeCounts; before: OutcomeCounts | null; beforeText: string | null }) {
  const total = totalOf(counts);
  const share = sharesOf(counts)[outcome];
  const delta = beforeText === null ? null : changeInPoints(counts, before, outcome);
  const Icon = OUTCOME_ICON[outcome];
  return (
    <li className="min-w-0 rounded-xl bg-sunken p-4" data-outcome={outcome}>
      <p className="flex items-center gap-1.5 text-sm text-ink-2">
        <Icon className="size-4 shrink-0" style={{ color: OUTCOME_COLOR[outcome] }} aria-hidden="true" />
        {OUTCOME_LABEL[outcome]}
      </p>
      <p className={cn(METRIC_SIZE.tile, "mt-1.5 text-ink")} data-testid={`share-${outcome}`}>
        {total === 0 ? "–" : `${share}%`}
      </p>
      <p className="mt-2 text-sm text-ink-2" data-testid={`count-${outcome}`}>
        {total === 0 ? "no vehicles" : `${formatCount(counts[outcome])} of ${formatCount(total)}`}
      </p>
      {delta !== null && beforeText !== null && <Change outcome={outcome} points={delta} before={beforeText} />}
    </li>
  );
}

const TONE_COLOR: Record<Tone, string> = { good: "text-good", bad: "text-critical", neutral: "text-ink-3" };

/** A change in words with an arrow: the colour of the arrow only backs them up. */
function Change({ outcome, points, before }: { outcome: StopOutcome; points: number; before: string }) {
  const Arrow = points > 0 ? ArrowUp : points < 0 ? ArrowDown : Minus;
  return (
    <p className="mt-1 flex items-start gap-1.5 text-sm text-ink-2" data-testid={`change-${outcome}`}>
      <Arrow className={cn("mt-0.5 size-4 shrink-0", TONE_COLOR[changeTone(outcome, points)])} aria-hidden="true" />
      <span>
        {pointsText(points)} on {before}
      </span>
    </p>
  );
}

// --- highlights ----------------------------------------------------------------------------------

interface Fact {
  key: string;
  label: string;
  value: string;
  note: string;
}

function Highlights({ stats }: { stats: StopStats }) {
  const now = useNow(60_000);
  const total = totalOf(stats.totals);
  const facts: Fact[] = [];
  const hour = peak(stats.by_hour, totalOf);
  if (hour) {
    facts.push({
      key: "busiest-hour",
      label: "Busiest hour at stop signs",
      value: hourRange(hour.item.hour),
      note: `${plural(hour.value, "vehicle")}, ${percentOf(hour.value, total)}% of all`,
    });
  }
  const misses = peak(stats.by_hour, violationsOf);
  if (misses) {
    facts.push({
      key: "most-misses",
      label: "Most that did not fully stop",
      value: hourRange(misses.item.hour),
      note: `${plural(misses.value, "vehicle")} rolled through or did not stop`,
    });
  }
  const byHour = rateExtremes(stats.by_hour);
  if (byHour) {
    facts.push({
      key: "lowest-hour",
      label: "Hour with the lowest stop rate",
      value: hourRange(byHour.lowest.item.hour),
      note: `${byHour.lowest.percent}% of ${byHour.lowest.of} stopped`,
    });
  }
  if (stats.fastest) {
    const fastest = fastestText(stats.fastest, stats.units, stats.timezone, now);
    const measured = stats.fastest.calibrated;
    facts.push({
      key: "fastest",
      label: measured ? "Fastest speed" : "Fastest estimated speed",
      value: fastest.speed,
      note: `${fastest.note}.${measured ? "" : " A rough estimate."}`,
    });
  }
  const byKind = rateExtremes(stats.by_kind);
  if (byKind) {
    facts.push({
      key: "lowest-kind",
      label: "Least likely to stop",
      value: classLabel(byKind.lowest.item.label),
      note: `${byKind.lowest.percent}% of ${byKind.lowest.of} stopped`,
    });
  }
  return (
    <Card>
      <CardHeader>
        <CardTitle as="h3">Highlights</CardTitle>
      </CardHeader>
      <CardBody>
        {facts.length === 0 ? (
          <p className="text-sm text-ink-2">Nothing to pick out yet.</p>
        ) : (
          <dl className="grid gap-x-6 gap-y-4 sm:grid-cols-2 lg:grid-cols-3">
            {facts.map((fact) => (
              <div key={fact.key} data-testid={`fact-${fact.key}`}>
                <dt className="text-sm text-ink-2">{fact.label}</dt>
                <dd className="mt-0.5">
                  <span className="block text-lg font-semibold text-ink">{fact.value}</span>
                  <span className="block text-sm text-ink-2">{fact.note}</span>
                </dd>
              </div>
            ))}
          </dl>
        )}
        <HowItWorks title="How these are calculated" className="mt-4">
          <p>
            An hour or a kind is compared only when at least {MIN_SAMPLE} of its vehicles could be judged, so that one out of one is not &ldquo;100%&rdquo;. A stop rate is the share
            of those vehicles that stopped.
          </p>
          {stats.fastest && <p>{`${stats.fastest.calibrated ? MEASURED_CAVEAT : ESTIMATE_CAVEAT} Each day's fastest is in the table of the day-by-day card.`}</p>}
        </HowItWorks>
      </CardBody>
    </Card>
  );
}

// --- tables --------------------------------------------------------------------------------------

interface BreakdownRow extends OutcomeCounts {
  key: string;
  name: string;
}

/** What each group of vehicles did, as percentages of its own vehicles; the vehicles are counted in the first column. */
function BreakdownTable({ title, firstHeader, rows, testId }: { title: string; firstHeader: string; rows: BreakdownRow[]; testId: string }) {
  const nonEmpty = rows.filter((row) => totalOf(row) > 0);
  return (
    <Card data-testid={testId}>
      <CardHeader>
        <CardTitle as="h3">{title}</CardTitle>
      </CardHeader>
      <CardBody>
        {nonEmpty.length === 0 ? (
          <p className="text-sm text-ink-2">No vehicle was judged in this time.</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">{title}: how many vehicles, and the share of them that did each thing</caption>
              <thead>
                <tr className="text-left text-ink-2">
                  <th scope="col" className="pb-1.5 pr-3 font-medium">{firstHeader}</th>
                  <th scope="col" className="hidden pb-1.5 pr-3 text-right font-medium sm:table-cell">Vehicles</th>
                  {OUTCOMES.map((outcome) => (
                    <th key={outcome} scope="col" className="pb-1.5 pr-2 text-right font-medium last:pr-0 sm:pr-3">{OUTCOME_LABEL[outcome]}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {nonEmpty.map((row) => {
                  const total = totalOf(row);
                  const shares = sharesOf(row);
                  return (
                    <tr key={row.key} className="border-t border-grid">
                      <th scope="row" className="py-1.5 pr-2 text-left font-normal text-ink sm:pr-3">
                        {row.name}
                        <span className="block text-xs text-ink-3 sm:hidden">{plural(total, "vehicle")}</span>
                      </th>
                      <td className="hidden py-1.5 pr-3 text-right tabular-nums text-ink sm:table-cell">{formatCount(total)}</td>
                      {OUTCOMES.map((outcome) => (
                        <td key={outcome} className="py-1.5 pr-2 text-right tabular-nums text-ink last:pr-0 sm:pr-3" title={plural(row[outcome], "vehicle")}>
                          {shares[outcome]}%
                        </td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </CardBody>
    </Card>
  );
}

// --- how they stop, and the reviews --------------------------------------------------------------

function HowTheyStop({ stats }: { stats: StopStats }) {
  const { stopped_hold_s, rolling_min_speed, did_not_stop_min_speed, rolling_min_speed_ms, did_not_stop_min_speed_ms } = stats.timing;
  // A speed measured on the calibrated road is used when there is one; otherwise the estimate from
  // the vehicle's size.
  const slowedTo = (measuredMs: number | null, sizesPerSecond: number | null): string | null =>
    typeof measuredMs === "number"
      ? `slowed to ${speedPhrase(measuredMs, stats.units, true)}`
      : sizesPerSecond === null
        ? null
        : `slowed to ${speedText(sizesPerSecond, stats.units)}`;
  const measured = typeof rolling_min_speed_ms === "number" || typeof did_not_stop_min_speed_ms === "number";
  const estimated =
    (typeof rolling_min_speed_ms !== "number" && typeof rolling_min_speed === "number") || (typeof did_not_stop_min_speed_ms !== "number" && typeof did_not_stop_min_speed === "number");
  const lines: { outcome: StopOutcome; text: string | null }[] = [
    { outcome: "stopped", text: stopped_hold_s === null ? null : `stood still for ${stopped_hold_s.toFixed(1)} s` },
    { outcome: "rolling", text: slowedTo(rolling_min_speed_ms, rolling_min_speed) },
    { outcome: "did_not_stop", text: slowedTo(did_not_stop_min_speed_ms, did_not_stop_min_speed) },
  ];
  const any = lines.some((line) => line.text !== null);
  return (
    <Card data-testid="data-stops-timing">
      <CardHeader>
        <CardTitle as="h3">How vehicles stop</CardTitle>
      </CardHeader>
      <CardBody>
        {!any ? (
          <p className="text-sm text-ink-2">Nothing to show yet.</p>
        ) : (
          <>
            <p className="mb-2 text-sm text-ink-2">What a typical vehicle did (the middle value):</p>
            <ul className="grid gap-2 text-sm">
              {lines.map(({ outcome, text }) => {
                const Icon = OUTCOME_ICON[outcome];
                return (
                  <li key={outcome} className="flex items-start gap-2">
                    <Icon className="mt-0.5 size-4 shrink-0" style={{ color: OUTCOME_COLOR[outcome] }} aria-hidden="true" />
                    <span className="text-ink">
                      <span className="text-ink-2">{OUTCOME_LABEL[outcome]}: </span>
                      {text ?? "no vehicle to go by"}
                    </span>
                  </li>
                );
              })}
            </ul>
            <p className="mt-3 text-sm text-ink-3">
              {measured
                ? `${MEASURED_CAVEAT}${estimated ? " A speed with “about” before it is an estimate from the vehicle’s own size, good to about 40%; it is not a measurement." : ""}`
                : "Speeds are rough: they are measured in the vehicle’s own sizes a second (a size is the width and the height of its box in the picture, averaged), and here a car’s size is taken to be about 2.5 m. They are good to about 40%."}
            </p>
          </>
        )}
      </CardBody>
    </Card>
  );
}

function Reviews({ stats }: { stats: StopStats }) {
  const { confirmed, false_alarms, waiting } = stats.review;
  const looked = confirmed + false_alarms;
  return (
    <Card data-testid="data-stops-reviews">
      <CardHeader>
        <CardTitle as="h3">Review progress</CardTitle>
      </CardHeader>
      <CardBody>
        <dl className="grid grid-cols-3 gap-3">
          <ReviewCount label="Confirmed" value={confirmed} />
          <ReviewCount label="False alarms" value={false_alarms} />
          <ReviewCount label="Needs review" value={waiting} />
        </dl>
        <p className="mt-3 text-sm text-ink-2">
          {looked === 0
            ? "No reviews yet."
            : `${percentOf(false_alarms, looked)}% of the ${plural(looked, "vehicle")} you reviewed were false alarms: how often the camera got it wrong.`}{" "}
          <Link href="/history/" className="font-medium text-accent underline underline-offset-2">
            Review passes &rarr;
          </Link>
        </p>
        <p className="mt-1 text-sm text-ink-3">Only rolled-through, did-not-stop and unclear passes are queued for review.</p>
      </CardBody>
    </Card>
  );
}

function ReviewCount({ label, value }: { label: string; value: number }) {
  return (
    <div>
      <dt className="text-sm text-ink-2">{label}</dt>
      <dd className="mt-0.5 text-2xl font-semibold text-ink">{formatCount(value)}</dd>
    </div>
  );
}

