import { InfoTip } from "@/components/ui/info-tip";
import { cn } from "@/lib/cn";

/**
 * A field's label. With a `hint`, an (i) beside it opens the explanation in a bubble; give the field
 * `aria-describedby={hintId}` so that it is described by it too.
 */
export function Label({
  className,
  hint,
  hintId,
  children,
  ...props
}: React.ComponentProps<"label"> & { hint?: React.ReactNode; hintId?: string }) {
  if (!hint) {
    return (
      <label className={cn("mb-1.5 block text-sm font-medium text-ink", className)} {...props}>
        {children}
      </label>
    );
  }
  // In the line of text, not beside it, so that a label that wraps has its icon after its last word.
  return (
    <div className="mb-1.5">
      <label className={cn("text-sm font-medium text-ink", className)} {...props}>
        {children}
      </label>
      <InfoTip id={hintId} className="-my-0.5 ml-1">
        {hint}
      </InfoTip>
    </div>
  );
}

export function Input({ className, ...props }: React.ComponentProps<"input">) {
  return (
    <input
      className={cn(
        "h-10 w-full rounded-lg border border-baseline bg-surface px-3 text-ink placeholder:text-ink-3 disabled:opacity-60",
        className,
      )}
      {...props}
    />
  );
}
