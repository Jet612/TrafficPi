"use client";

import Link from "next/link";
import { ClassSplit } from "@/components/class-split";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { METRIC_SIZE } from "@/components/ui/stat";
import { useStats } from "@/components/stats-provider";
import { formatCount } from "@/lib/format";
import type { TodaySummary } from "@/lib/types";

export function TodayCard() {
  const { today, loaded, failed } = useStats();
  return (
    <Card className="flex flex-col">
      <CardHeader>
        <div>
          <CardTitle>Counted today</CardTitle>
          <CardSubtitle>Crossed a counting line since midnight.</CardSubtitle>
        </div>
      </CardHeader>
      <CardBody className="flex flex-1 flex-col gap-6">
        {!loaded && <p className="text-sm text-ink-2">Loading&hellip;</p>}
        {failed && <p className="text-sm text-ink">Today&rsquo;s numbers could not be loaded. Trying again.</p>}
        {today && <TodayBody today={today} />}
      </CardBody>
    </Card>
  );
}

function TodayBody({ today }: { today: TodaySummary }) {
  return (
    <>
      <div>
        {/* The one number this page leads with. */}
        <p className={`${METRIC_SIZE.hero} text-ink`} data-testid="today-total">
          {formatCount(today.total)}
        </p>
        <p className="mt-2 text-sm text-ink-2">
          {today.lines.length === 0 ? (
            <>
              {today.total === 0 ? "No counting lines yet." : "All of these were counted by lines that have since been removed."}{" "}
              <Link href="/setup/" className="font-medium text-accent underline underline-offset-2">
                Add a line on the Setup page
              </Link>
              {today.total === 0 ? "." : " to count again."}
            </>
          ) : today.total === 0 ? (
            "Nothing has crossed a counting line yet today."
          ) : (
            `across ${today.lines.length} counting line${today.lines.length === 1 ? "" : "s"}`
          )}
        </p>
      </div>
      <ClassSplit counts={today.by_class} label="Counted today by kind" />
    </>
  );
}
