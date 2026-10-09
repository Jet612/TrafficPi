import { Check, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/cn";
import { REVIEW_LABEL } from "@/lib/stops";
import type { ReviewState } from "@/lib/types";

/**
 * What a person makes of one pass at a stop sign: it was so (confirmed) or the program was wrong (a false alarm).
 * Pressing the one that is set takes it back. `what` says which pass this is, for the buttons' names.
 */
export function ReviewButtons({
  review,
  busy,
  what,
  onReview,
  className,
}: {
  review: ReviewState | null;
  busy: boolean;
  what: string;
  onReview: (pressed: ReviewState) => void;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)} role="group" aria-label={`Your say on the ${what}`}>
      {(["confirmed", "false_positive"] as const).map((state) => (
        <Button
          key={state}
          size="sm"
          variant="secondary"
          disabled={busy}
          aria-pressed={review === state}
          aria-label={`${state === "confirmed" ? "Confirm" : "False alarm"}: ${what}`}
          onClick={() => onReview(state)}
          className={review === state ? "bg-accent-wash" : undefined}
        >
          {state === "confirmed" ? <Check className="size-4" aria-hidden="true" /> : <X className="size-4" aria-hidden="true" />}
          {state === "confirmed" ? "Confirm" : "False alarm"}
        </Button>
      ))}
      {review && <span className="sr-only">{REVIEW_LABEL[review]}</span>}
    </div>
  );
}
