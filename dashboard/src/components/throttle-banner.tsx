"use client";

import { CircleX, TriangleAlert } from "lucide-react";
import { useLiveSelector } from "@/components/live-provider";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { HARD_LIMIT_C, minutesText } from "@/lib/health";
import { useRequest } from "@/lib/hooks";
import type { LiveSnapshot } from "@/lib/live";
import { describeThrottle } from "@/lib/severity";

const REFRESH_MS = 60_000;
/** The stretch the banner looks back over, whatever period the health card below is set to. */
const LOOKBACK_HOURS = 24;

const selectTrouble = (snapshot: LiveSnapshot): string => (snapshot.state?.system.throttled_now ?? []).join("|");

/**
 * The first thing the System page says when the Pi has been struggling: that it was throttled in the
 * last day, for how long and how hot it got, and what is wrong now. A caption at the end of a card did
 * not say it loudly enough, and the header's pill only knows about right now.
 */
export function ThrottleBanner() {
  const history = useRequest(`system-history:banner`, (signal) => api.systemHistory(LOOKBACK_HOURS, signal), { refreshMs: REFRESH_MS });
  const trouble = useLiveSelector(selectTrouble);
  const summary = history.data?.summary;
  const throttledMinutes = summary?.slowed_minutes ?? 0;
  const weakMinutes = summary?.under_voltage_minutes ?? 0;
  const nowFlags = trouble === "" ? [] : trouble.split("|");
  if (throttledMinutes === 0 && weakMinutes === 0 && nowFlags.length === 0) return null;

  const peak = summary?.temp_c_max ?? null;
  const hard = peak !== null && peak >= HARD_LIMIT_C && throttledMinutes > 0;
  const Icon = hard ? CircleX : TriangleAlert;
  const parts: string[] = [];
  if (throttledMinutes > 0) parts.push(`Throttled for ${minutesText(throttledMinutes * 60)} in the last 24 hours${peak !== null ? `, up to ${peak.toFixed(1)} °C` : ""}.`);
  if (weakMinutes > 0) parts.push(`The power supply was too weak for ${minutesText(weakMinutes * 60)}.`);
  const now = nowFlags.map(describeThrottle).join("; ");
  return (
    <div
      role="status"
      data-testid="throttle-banner"
      className={cn(
        "flex items-start gap-3 rounded-xl border p-4",
        hard ? "border-critical/60 bg-[color-mix(in_oklab,var(--critical)_12%,var(--surface))]" : "border-warning/60 bg-[color-mix(in_oklab,var(--warning)_14%,var(--surface))]",
      )}
    >
      <Icon className={cn("mt-0.5 size-5 shrink-0", hard ? "text-critical" : "text-warning")} aria-hidden="true" />
      <div className="min-w-0 text-sm">
        <p className="font-medium text-ink">{throttledMinutes > 0 || weakMinutes > 0 ? "The Pi has been struggling." : "The Pi is struggling now."}</p>
        <p className="mt-0.5 text-ink-2">
          {parts.join(" ")}
          {now && <> Right now: {now}.</>}{" "}
          {history.data && history.data.episodes.length > 0 && (
            <a href="#health-episodes" className="font-medium text-accent underline underline-offset-2">
              See what happened
            </a>
          )}
        </p>
      </div>
    </div>
  );
}
