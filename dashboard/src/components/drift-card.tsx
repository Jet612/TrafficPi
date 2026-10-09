"use client";

import { selectDrift } from "@/components/drift-banner";
import { useLiveSelector } from "@/components/live-provider";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { StatTile } from "@/components/ui/stat";
import { StatusBadge } from "@/components/ui/status";
import { describeChecks, describeMove, describeSize, describeTurn, driftLabel, driftLevel, sameDrift } from "@/lib/drift";
import { useNow } from "@/lib/hooks";

/** Whether the camera still sees what it saw when the reference picture was kept (plan.md D40). */
export function DriftCard() {
  const drift = useLiveSelector(selectDrift, sameDrift);
  const now = useNow(30_000);
  const shift = drift?.shift_pct ?? null;

  return (
    <Card>
      <CardHeader>
        <CardTitle>Camera view</CardTitle>
        <StatusBadge level={driftLevel(drift)}>{driftLabel(drift)}</StatusBadge>
      </CardHeader>
      <CardBody>
        {drift?.message && (
          <p data-testid="drift-message" className="max-w-3xl text-sm text-ink">
            {drift.message}
          </p>
        )}
        {drift !== null && shift !== null && (
          <dl className="mt-3 grid grid-cols-2 gap-3 md:grid-cols-4" aria-label="How far the view has moved">
            <StatTile label="Lines and zones off by" value={`${shift.toFixed(1)}%`} note="of the picture's width, at most" />
            <StatTile label="Picture moved" value={describeMove(drift) || "none"} wrap />
            <StatTile label="Turned" value={describeTurn(drift) || "none"} wrap />
            <StatTile label="Size changed" value={describeSize(drift) || "none"} wrap />
          </dl>
        )}
        {drift?.unsure_because && <p className="mt-3 text-sm text-ink-2">The latest look could not tell: {drift.unsure_because}.</p>}
        <p className="mt-3 text-sm text-ink-2">{describeChecks(drift, now)}</p>
        <p className="mt-3 max-w-3xl text-sm text-ink-2">
          Once a minute the live picture is compared with the reference picture that the lines and zones were drawn on, by matching what stays
          put: the houses, the kerb, the poles. If they have moved, the counts and the stop-sign results are made against the wrong picture,
          so this says so. At night, in fog or with something in front of the lens it cannot tell, and then it keeps what it last knew. It only
          warns: nothing it finds changes what is counted.
        </p>
      </CardBody>
    </Card>
  );
}
