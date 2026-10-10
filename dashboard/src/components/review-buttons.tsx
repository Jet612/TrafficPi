import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { REVIEW_LABEL } from "@/lib/stops";
import type { ReviewState } from "@/lib/types";

/**
 * What a person makes of one pass at a stop sign: it was so (confirmed) or the program was wrong (a
 * false alarm). `what` says which pass this is, for the buttons' names.
 *
 * In a list (`compact`) an unreviewed pass has two icon buttons, and a reviewed one says what it was
 * reviewed as, with an Undo. Alone (the window that opens a pass) the two buttons have their words
 * and pressing the one that is set takes it back.
 */
export function ReviewButtons({
  review,
  busy,
  what,
  onReview,
  className,
  compact = false,
}: {
  review: ReviewState | null;
  busy: boolean;
  what: string;
  onReview: (pressed: ReviewState) => void;
  className?: string;
  compact?: boolean;
}) {
  if (compact) {
    if (review !== null) {
      return (
        <div className={cn("flex items-center gap-1", className)} role="group" aria-label={`Review of the ${what}`}>
          <span className="inline-flex items-center gap-1.5 rounded-lg bg-sunken px-2.5 py-1 text-sm font-medium text-ink">
            {review === "confirmed" ? <Check className="size-4" aria-hidden="true" /> : <X className="size-4" aria-hidden="true" />}
            {REVIEW_LABEL[review]}
          </span>
          <Button size="sm" variant="ghost" disabled={busy} aria-label={`Undo the review: ${what}`} onClick={() => onReview(review)}>
            Undo
          </Button>
        </div>
      );
    }
    return (
      <div className={cn("flex items-center gap-1.5", className)} role="group" aria-label={`Review of the ${what}`}>
        {(["confirmed", "false_positive"] as const).map((state) => (
          <Button
            key={state}
            size="sm"
            variant="secondary"
            disabled={busy}
            aria-label={`${state === "confirmed" ? "Confirm" : "False alarm"}: ${what}`}
            title={state === "confirmed" ? "Confirm: the program got it right" : "False alarm: the program got it wrong"}
            onClick={() => onReview(state)}
            className="size-9 px-0 pointer-coarse:size-10"
          >
            {state === "confirmed" ? <Check className="size-4" aria-hidden="true" /> : <X className="size-4" aria-hidden="true" />}
          </Button>
        ))}
      </div>
    );
  }
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)} role="group" aria-label={`Review of the ${what}`}>
      {(["confirmed", "false_positive"] as const).map((state) => (
        <Button
          key={state}
          size="sm"
          variant="secondary"
          disabled={busy}
          aria-pressed={review === state}
          aria-label={`${state === "confirmed" ? "Confirm" : "False alarm"}: ${what}`}
          onClick={() => onReview(state)}
          className={review === state ? "border-accent bg-accent-wash" : undefined}
        >
          {state === "confirmed" ? <Check className="size-4" aria-hidden="true" /> : <X className="size-4" aria-hidden="true" />}
          {state === "confirmed" ? "Confirm" : "False alarm"}
        </Button>
      ))}
      {review && <span className="sr-only">{REVIEW_LABEL[review]}</span>}
    </div>
  );
}
