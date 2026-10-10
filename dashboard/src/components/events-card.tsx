"use client";

import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { ClassIcon } from "@/components/ui/class-icon";
import { DirectionArrow } from "@/components/ui/direction-arrow";
import { useLiveSelector } from "@/components/live-provider";
import { useStats } from "@/components/stats-provider";
import { classSingular } from "@/lib/classes";
import { crossingHeading } from "@/lib/direction";
import { formatClock } from "@/lib/format";
import { sameLines, selectLines } from "@/lib/live-select";

export function EventsCard() {
  const { today, events, loaded } = useStats();
  const lines = useLiveSelector(selectLines, sameLines);
  const zone = today?.timezone ?? "UTC";
  const names = new Map((today?.lines ?? []).map((line) => [line.id, line.name || line.id]));
  const geometry = new Map((lines ?? []).map((line) => [line.id, line]));
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <div>
          <CardTitle>Latest counts</CardTitle>
          <CardSubtitle>Newest first, to the second.</CardSubtitle>
        </div>
      </CardHeader>
      <CardBody className="flex-1">
        {events === null ? (
          <p className="text-sm text-ink-2">{loaded ? "No data yet." : "Loading…"}</p>
        ) : events.length === 0 ? (
          <p className="text-sm text-ink-2">Nothing has been counted yet. New counts appear here as they happen.</p>
        ) : (
          <ol className="flex flex-col">
            {events.map((event) => {
              const kind = event.label ?? "";
              const forward = event.direction !== -1;
              const shape = geometry.get(event.line ?? "");
              return (
                <li key={event.id} className="flex items-center gap-2.5 border-t border-grid py-2 text-sm first:border-t-0 first:pt-0">
                  <time className="w-[4.25rem] shrink-0 tabular-nums text-ink-2" dateTime={new Date(event.t).toISOString()}>
                    {formatClock(event.t, zone)}
                  </time>
                  <ClassIcon name={kind} />
                  <span className="min-w-0 flex-1 truncate text-ink">
                    {classSingular(kind)}
                    {/* With one line the name says nothing new; with several it says which. */}
                    {names.size > 1 && <span className="text-ink-2"> &middot; {names.get(event.line ?? "") ?? event.line}</span>}
                  </span>
                  <span className="inline-flex shrink-0 items-center gap-1 text-ink-2">
                    {shape ? (
                      <DirectionArrow heading={crossingHeading(shape, forward)} />
                    ) : (
                      <DirectionArrow heading={forward ? "right" : "left"} />
                    )}
                    {forward ? "Forward" : "Reverse"}
                  </span>
                </li>
              );
            })}
          </ol>
        )}
      </CardBody>
    </Card>
  );
}
