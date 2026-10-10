import { cva, type VariantProps } from "class-variance-authority";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/cn";

const button = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-medium transition-colors disabled:pointer-events-none",
  {
    variants: {
      variant: {
        // A primary button that cannot be pressed turns neutral, so that it does not look half active.
        primary:
          "bg-accent text-accent-ink hover:brightness-110 active:brightness-95 disabled:border disabled:border-edge disabled:bg-sunken disabled:text-ink-3 disabled:brightness-100",
        secondary: "border border-edge bg-surface text-ink hover:bg-sunken disabled:opacity-50",
        ghost: "text-ink-2 hover:bg-sunken hover:text-ink disabled:opacity-50",
      },
      // Under a finger (a coarse pointer) every size is at least 40 px tall.
      size: { md: "h-10 px-4", sm: "h-8 px-3 pointer-coarse:h-10" },
    },
    defaultVariants: { variant: "secondary", size: "md" },
  },
);

/** A primary button that is working keeps its colour: the spinner says it cannot be pressed again. */
const BUSY_PRIMARY = "disabled:border-transparent disabled:bg-accent disabled:text-accent-ink disabled:opacity-60";

type Props = React.ComponentProps<"button"> & VariantProps<typeof button> & { busy?: boolean };

export function Button({ className, variant, size, busy = false, disabled, children, ...props }: Props) {
  return (
    <button
      className={cn(button({ variant, size }), busy && variant === "primary" && BUSY_PRIMARY, className)}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      {...props}
    >
      {busy && <Loader2 className="size-4 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  );
}
