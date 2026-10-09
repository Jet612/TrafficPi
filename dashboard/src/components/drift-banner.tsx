"use client";

import { TriangleAlert } from "lucide-react";
import Link from "next/link";
import { useLiveSelector } from "@/components/live-provider";
import { sameDrift, type DriftStatus } from "@/lib/drift";
import type { LiveSnapshot } from "@/lib/live";

export const selectDrift = (snapshot: LiveSnapshot): DriftStatus | null => snapshot.state?.drift ?? null;

/**
 * A notice on every page while the camera's view has moved since the reference picture was kept
 * (plan.md D40). The lines, zones and stop signs are drawn on that picture, so they no longer fit
 * the street; the notice goes when the view is back, or when a new picture is kept.
 */
export function DriftBanner() {
  const drift = useLiveSelector(selectDrift, sameDrift);
  if (drift?.state !== "drifted") return null;
  return (
    <div role="alert" data-testid="drift-banner" className="border-b border-edge bg-sunken">
      <div className="mx-auto flex max-w-[1280px] items-start gap-3 px-4 py-3 sm:px-6">
        <TriangleAlert className="mt-0.5 size-5 shrink-0 text-warning" aria-hidden="true" />
        <div className="min-w-0 text-sm">
          <p className="font-medium text-ink">{drift.message}</p>
          <p className="mt-1 text-ink-2">
            Counts and stop-sign results made while the view is off may be wrong. If you moved the camera on purpose, check the lines
            and zones on the{" "}
            <Link href="/setup/" className="font-medium text-ink underline underline-offset-2">
              Setup page
            </Link>{" "}
            and choose &ldquo;Replace the saved picture&rdquo;. If not, put the camera back.
          </p>
        </div>
      </div>
    </div>
  );
}
