import { cn } from "@/lib/cn";

/**
 * Two or more choices side by side, one of them pressed: for how to look at something. A label never
 * wraps onto a second line; a row that is wider than its room scrolls sideways instead. The pressed
 * choice is the same wash and edge as every other selected thing (a tile, a day button).
 */
export function Segmented<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: T;
  options: readonly { value: T; label: string }[];
  onChange: (value: T) => void;
  className?: string;
}) {
  return (
    <div
      role="group"
      aria-label={label}
      className={cn("flex min-w-0 max-w-full overflow-x-auto rounded-lg border border-edge p-0.5 text-sm [scrollbar-width:none]", className)}
    >
      {options.map((option) => {
        const pressed = value === option.value;
        return (
          <button
            key={option.value}
            type="button"
            aria-pressed={pressed}
            onClick={() => onChange(option.value)}
            className={cn(
              "shrink-0 whitespace-nowrap rounded-md px-3 py-1 font-medium transition-colors pointer-coarse:py-2",
              pressed ? "bg-accent-wash text-ink shadow-[inset_0_0_0_1px_var(--accent)]" : "text-ink-2 hover:text-ink",
            )}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
