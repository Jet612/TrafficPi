"use client";

import { ArrowDownLeft, ArrowUpRight } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { useStats } from "@/components/stats-provider";
import { classColor, classSingular } from "@/lib/classes";
import { formatClock } from "@/lib/format";

export function EventsCard() {
  const { today, events, loaded } = useStats();
  const zone = today?.timezone ?? "UTC";
  const names = new Map((today?.lines ?? []).map((line) => [line.id, line.name || line.id]));
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <CardTitle>Latest counts</CardTitle>
        {today && <span className="text-sm text-ink-2">times in {zone.replace("_", " ")}</span>}
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
              return (
                <li key={event.id} className="flex items-center gap-3 border-t border-grid py-2 text-sm first:border-t-0 first:pt-0">
                  <time className="w-[4.5rem] shrink-0 tabular-nums text-ink-2" dateTime={new Date(event.t).toISOString()}>
                    {formatClock(event.t, zone)}
                  </time>
                  <span className="h-[3px] w-3 shrink-0 rounded-full" style={{ background: classColor(kind) }} aria-hidden="true" />
                  <span className="min-w-0 flex-1 truncate text-ink">
                    {classSingular(kind)}
                    {/* With one line the name says nothing new; with several it says which. */}
                    {names.size > 1 && <span className="text-ink-2"> &middot; {names.get(event.line ?? "") ?? event.line}</span>}
                  </span>
                  <span className="inline-flex shrink-0 items-center gap-1 text-ink-2">
                    {forward ? <ArrowUpRight className="size-4" aria-hidden="true" /> : <ArrowDownLeft className="size-4" aria-hidden="true" />}
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
