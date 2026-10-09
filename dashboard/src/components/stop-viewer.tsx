"use client";

import { ChevronLeft, ChevronRight, Download } from "lucide-react";
import { ReviewButtons } from "@/components/review-buttons";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { StatusBadge } from "@/components/ui/status";
import { classSingular } from "@/lib/classes";
import { formatDateTimeSeconds } from "@/lib/format";
import { OUTCOME_LABEL, OUTCOME_LEVEL, numbersText, passText } from "@/lib/stops";
import type { ReviewState, StopOut } from "@/lib/types";

/** Whether the person asked their device for less motion: then a clip waits to be played instead of starting by itself. */
function prefersLessMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * One pass at a stop sign, with its clip to watch (or its picture, if there is no clip) and the review buttons, so that
 * a person can look and decide in one place. Newer and Older go through the passes in the list behind it.
 */
export function StopViewer({
  row,
  sign,
  zone,
  busy,
  newer,
  older,
  onShow,
  onReview,
  onClose,
}: {
  row: StopOut;
  sign: string;
  zone: string;
  busy: boolean;
  newer: StopOut | null;
  older: StopOut | null;
  onShow: (row: StopOut) => void;
  onReview: (pressed: ReviewState) => void;
  onClose: () => void;
}) {
  const what = passText(row, sign, zone);
  return (
    <Dialog title={`${classSingular(row.label ?? "")} at ${sign}`} onClose={onClose}>
      <div className="grid gap-3">
        <p className="flex flex-wrap items-center gap-x-3 gap-y-1.5 text-sm">
          <time className="tabular-nums text-ink-2" dateTime={new Date(row.t).toISOString()}>
            {formatDateTimeSeconds(row.t, zone)}
          </time>
          <StatusBadge level={OUTCOME_LEVEL[row.outcome]}>{OUTCOME_LABEL[row.outcome]}</StatusBadge>
          <span className="text-ink-3">{numbersText(row)}</span>
        </p>

        {row.clip_url !== null ? (
          <video
            key={row.id}
            src={row.clip_url}
            poster={row.snapshot_url ?? undefined}
            aria-label={`Clip of the ${what}`}
            controls
            muted
            loop
            playsInline
            autoPlay={!prefersLessMotion()}
            preload="metadata"
            className="aspect-video w-full rounded-lg bg-black"
          />
        ) : row.snapshot_url !== null ? (
          // eslint-disable-next-line @next/next/no-img-element -- a picture from the API, kept as it is
          <img key={row.id} src={row.snapshot_url} alt={`Picture of the ${what}`} className="aspect-video w-full rounded-lg bg-black object-contain" />
        ) : (
          <p className="grid aspect-video w-full place-items-center rounded-lg border border-dashed border-edge px-6 text-center text-sm text-ink-2">
            No picture or clip is kept of this pass. Evidence is kept only for the passes that need a look, only while it is switched on in the settings, and only for so long.
          </p>
        )}

        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <ReviewButtons review={row.review} busy={busy} what={what} onReview={onReview} />
          <div className="flex items-center gap-2" role="group" aria-label="Other passes in the list">
            <Button size="sm" variant="ghost" disabled={newer === null} onClick={() => newer && onShow(newer)}>
              <ChevronLeft className="size-4" aria-hidden="true" />
              Newer
            </Button>
            <Button size="sm" variant="ghost" disabled={older === null} onClick={() => older && onShow(older)}>
              Older
              <ChevronRight className="size-4" aria-hidden="true" />
            </Button>
          </div>
        </div>

        {(row.clip_url !== null || row.snapshot_url !== null) && (
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
            {row.clip_url !== null && (
              <a href={row.clip_url} download={`stop-${row.id}.mp4`} className="inline-flex items-center gap-1.5 font-medium text-accent underline underline-offset-2">
                <Download className="size-4" aria-hidden="true" />
                Save the clip
              </a>
            )}
            {row.snapshot_url !== null && (
              <a href={row.snapshot_url} target="_blank" rel="noreferrer" className="font-medium text-accent underline underline-offset-2">
                Open the picture on its own
              </a>
            )}
          </p>
        )}
      </div>
    </Dialog>
  );
}
