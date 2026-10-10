"use client";

import { useState } from "react";
import Link from "next/link";
import { SpeedStatsSection } from "@/components/speed-stats";
import { StopHoursNote } from "@/components/stop-hours-note";
import { StopStatsSection } from "@/components/stop-stats";
import { TrafficStatsSection } from "@/components/traffic-stats";
import { Button } from "@/components/ui/button";
import { Card, CardBody } from "@/components/ui/card";
import { Segmented } from "@/components/ui/segmented";
import { api } from "@/lib/api";
import { DATA_PERIODS, periodDays, totalOf, type DataPeriod } from "@/lib/data";
import { usePersistentState, useRequest } from "@/lib/hooks";
import { signLabel } from "@/lib/stops";

const REFRESH_MS = 30_000;

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

  return (
    <div className="grid grid-cols-1 gap-6">
      <h1 className="sr-only">Data</h1>

      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <Segmented
          label="Period"
          value={period}
          options={DATA_PERIODS.map(({ value, label }) => ({ value, label }))}
          onChange={(value) => setStored({ ...stored, period: value })}
        />
        {signs.length > 1 && (
          <select
            aria-label="Stop sign"
            value={sign}
            onChange={(event) => setPickedSign(event.target.value)}
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

      <section aria-labelledby="data-stops-heading" className="grid gap-4">
        <h2 id="data-stops-heading" className="text-base font-semibold text-ink">
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

      <section aria-labelledby="data-traffic-heading" className="grid gap-4">
        <h2 id="data-traffic-heading" className="text-base font-semibold text-ink">
          Traffic
        </h2>
        {traffic.data !== null ? (
          <TrafficStatsSection stats={traffic.data} busy={trafficBusy} />
        ) : (
          <Loading what="The traffic numbers" failed={traffic.error !== null} retry={traffic.refresh} />
        )}
      </section>

      <section aria-labelledby="data-speeds-heading" className="grid gap-4">
        <h2 id="data-speeds-heading" className="text-base font-semibold text-ink">
          Speeds
        </h2>
        {speeds.data !== null ? (
          <SpeedStatsSection stats={speeds.data} busy={speedsBusy} />
        ) : (
          <Loading what="The speeds" failed={speeds.error !== null} retry={speeds.refresh} />
        )}
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
