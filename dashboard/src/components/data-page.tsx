"use client";

import { useState } from "react";
import Link from "next/link";
import { ExportCard } from "@/components/export-card";
import { SpeedStatsSection } from "@/components/speed-stats";
import { StopHoursNote } from "@/components/stop-hours-note";
import { StopStatsSection } from "@/components/stop-stats";
import { TrafficStatsSection } from "@/components/traffic-stats";
import { TrailsCard } from "@/components/trails-card";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import { api } from "@/lib/api";
import { DATA_PERIODS, periodDays, periodText, totalOf, type DataPeriod } from "@/lib/data";
import { usePersistentState, useRequest } from "@/lib/hooks";
import { signLabel } from "@/lib/stops";

const REFRESH_MS = 30_000;

const SECTIONS = [
  { id: "data-stops", label: "Stop signs" },
  { id: "data-traffic", label: "Traffic" },
  { id: "data-speeds", label: "Speeds" },
  { id: "data-trails", label: "Trails" },
  { id: "data-export", label: "Export" },
] as const;

/** The Data page: what the stop signs and the counting lines have seen, over a stretch of days chosen once for all of it. */
export function DataPage() {
  const [stored, setStored] = usePersistentState<{ period?: DataPeriod }>("trafficpi.data", {});
  const period: DataPeriod = DATA_PERIODS.some((option) => option.value === stored.period) ? (stored.period as DataPeriod) : "week";
  const [pickedSign, setPickedSign] = useState("all");
  const days = periodDays(period);

  const profile = useRequest("profile", (signal) => api.profile(signal));
  const signs = profile.data?.profile.stop_signs ?? [];
  const sign = signs.some((one) => one.id === pickedSign) ? pickedSign : "all"; // a sign that was deleted since is not asked about

  const stopsKey = `data-stops:${days}:${sign}`;
  const stops = useRequest(stopsKey, (signal) => api.stopStats(days, sign === "all" ? undefined : sign, signal), { refreshMs: REFRESH_MS });
  const trafficKey = `data-traffic:${days}`;
  const traffic = useRequest(trafficKey, (signal) => api.trafficStats(days, signal), { refreshMs: REFRESH_MS });
  const speedsKey = `data-speeds:${days}`;
  const speeds = useRequest(speedsKey, (signal) => api.speedStats(days, undefined, signal), { refreshMs: REFRESH_MS });

  const stopsBusy = stops.data !== null && stops.answeredKey !== stopsKey;
  const trafficBusy = traffic.data !== null && traffic.answeredKey !== trafficKey;
  const speedsBusy = speeds.data !== null && speeds.answeredKey !== speedsKey;
  const noSigns = profile.data !== null && signs.length === 0 && stops.data !== null && totalOf(stops.data.totals) === 0 && stops.data.review.false_alarms === 0;
  // The days the page covers, said once here and not in each card.
  const range = periodText((traffic.data ?? stops.data)?.by_day ?? [], (traffic.data ?? stops.data)?.all_time ?? false);

  return (
    <div className="grid grid-cols-1 gap-6">
      <h1 className="sr-only">Data</h1>

      {/* The period drives the whole page, so it stays in view and says which days it comes to; the cards do not say it again. */}
      <div className="sticky top-14 z-30 -mx-4 border-b border-edge bg-page/95 px-4 backdrop-blur sm:-mx-6 sm:px-6">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2">
          <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
            <Segmented
              label="Period"
              value={period}
              options={DATA_PERIODS.map(({ value, label }) => ({ value, label }))}
              onChange={(value) => setStored({ ...stored, period: value })}
            />
            <p className="text-sm tabular-nums text-ink-2" data-testid="data-range">
              {range}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
            {signs.length > 1 && (
              <select
                aria-label="Stop sign"
                value={sign}
                onChange={(event) => setPickedSign(event.target.value)}
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
            <nav aria-label="Sections of this page" className="hidden gap-3 text-sm font-medium md:flex">
              {SECTIONS.map((section) => (
                <a key={section.id} href={`#${section.id}`} className="text-ink-2 underline-offset-2 hover:text-ink hover:underline">
                  {section.label}
                </a>
              ))}
            </nav>
          </div>
        </div>
      </div>

      <section id="data-stops" aria-labelledby="data-stops-heading" className="grid scroll-mt-28 grid-cols-1 gap-4">
        <h2 id="data-stops-heading" className="text-xl font-semibold text-ink">
          Stop signs
        </h2>
        <StopHoursNote />
        {noSigns ? (
          <Card>
            <CardBody>
              <p className="max-w-2xl py-2 text-sm text-ink-2">
                No stop sign is set up yet.{" "}
                <Link href="/setup/" className="font-medium text-accent underline underline-offset-2">
                  Add one on the Setup page
                </Link>{" "}
                to see whether vehicles stop before its line, and when.
              </p>
            </CardBody>
          </Card>
        ) : stops.data !== null ? (
          <StopStatsSection stats={stops.data} period={period} profile={profile.data?.profile ?? null} busy={stopsBusy} />
        ) : (
          <Loading what="The stop-sign numbers" failed={stops.error !== null} retry={stops.refresh} />
        )}
      </section>

      <section id="data-traffic" aria-labelledby="data-traffic-heading" className="grid scroll-mt-28 grid-cols-1 gap-4">
        <h2 id="data-traffic-heading" className="text-xl font-semibold text-ink">
          Traffic
        </h2>
        {traffic.data !== null ? (
          <TrafficStatsSection stats={traffic.data} busy={trafficBusy} />
        ) : (
          <Loading what="The traffic numbers" failed={traffic.error !== null} retry={traffic.refresh} />
        )}
      </section>

      <section id="data-speeds" aria-labelledby="data-speeds-heading" className="grid scroll-mt-28 grid-cols-1 gap-4">
        <h2 id="data-speeds-heading" className="text-xl font-semibold text-ink">
          Speeds
        </h2>
        {speeds.data !== null ? (
          <SpeedStatsSection stats={speeds.data} busy={speedsBusy} />
        ) : (
          <Loading what="The speeds" failed={speeds.error !== null} retry={speeds.refresh} />
        )}
      </section>

      <section id="data-trails" aria-labelledby="data-trails-heading" className="grid scroll-mt-28 grid-cols-1 gap-4">
        <h2 id="data-trails-heading" className="text-xl font-semibold text-ink">
          Trails
        </h2>
        <p className="max-w-2xl text-sm text-ink-2">The path every vehicle took, one day at a time. The period above does not apply.</p>
        <TrailsCard />
      </section>

      <section id="data-export" aria-labelledby="data-export-heading" className="grid scroll-mt-28 grid-cols-1 gap-4">
        <h2 id="data-export-heading" className="text-xl font-semibold text-ink">
          Export
        </h2>
        <ExportCard days={days} range={range} />
      </section>
    </div>
  );
}

function Loading({ what, failed, retry }: { what: string; failed: boolean; retry: () => void }) {
  return (
    <Card>
      <CardBody>
        {failed ? (
          <p className="text-sm text-ink-2">
            {what} could not be loaded.{" "}
            <Button size="sm" variant="ghost" onClick={retry}>
              Try again
            </Button>
          </p>
        ) : (
          <p className="text-sm text-ink-2">Loading…</p>
        )}
      </CardBody>
    </Card>
  );
}
