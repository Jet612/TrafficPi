"use client";

import Link from "next/link";
import { Clock } from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/cn";
import { usePoll } from "@/lib/hooks";
import { noteText } from "@/lib/stop-hours";

const REFRESH_MS = 60_000;

/**
 * Where passes are shown: a line saying that stop signs are judged only at some hours, and where
 * that stands now, so that a quiet night is not taken for a street with no traffic. Nothing at all
 * when every hour counts (plan.md D42).
 */
export function StopHoursNote({ className }: { className?: string }) {
  const hours = usePoll((signal) => api.stopHours(signal), REFRESH_MS);
  const text = hours.data ? noteText(hours.data.status) : null;
  if (text === null) return null;
  return (
    <p className={cn("flex max-w-3xl items-start gap-2 text-sm text-ink-2", className)} data-testid="stop-hours-note">
      <Clock className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
      <span>
        {text}{" "}
        <Link href="/setup/" className="font-medium text-accent underline underline-offset-2">
          Change the hours
        </Link>
        .
      </span>
    </p>
  );
}
