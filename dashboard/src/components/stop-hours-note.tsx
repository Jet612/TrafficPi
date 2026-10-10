"use client";

import Link from "next/link";
import { Clock } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { usePoll } from "@/lib/hooks";
import { chipText } from "@/lib/stop-hours";

const REFRESH_MS = 60_000;

/**
 * Where passes are shown: one chip saying that stop signs are judged only at some hours, and until
 * when that holds ("Judging paused until 06:45"), so that a quiet night is not taken for a street with
 * no traffic. The hours themselves are in its tooltip and, in full, on the Setup page a link away.
 * Nothing at all when every hour counts (plan.md D42).
 */
export function StopHoursNote({ className }: { className?: string }) {
  const hours = usePoll((signal) => api.stopHours(signal), REFRESH_MS);
  const chip = hours.data ? chipText(hours.data.status) : null;
  if (chip === null) return null;
  return (
    <p className={cn("flex flex-wrap items-center gap-x-3 gap-y-1 text-sm", className)} data-testid="stop-hours-note">
      <span className="inline-flex items-center gap-1.5 rounded-lg bg-sunken px-2.5 py-1 text-ink" title={chip.title}>
        <Clock className="size-4 shrink-0 text-ink-3" aria-hidden="true" />
        {chip.text}
        <span className="sr-only">. {chip.title}</span>
      </span>
      <Link href="/setup/" className="font-medium text-accent underline underline-offset-2">
        Change hours
      </Link>
    </p>
  );
}
