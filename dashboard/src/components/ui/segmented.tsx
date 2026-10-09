import { cn } from "@/lib/cn";

/** Two or three choices side by side, one of them pressed: for how to look at something. */
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
    <div role="group" aria-label={label} className={cn("flex rounded-lg border border-edge p-0.5 text-sm", className)}>
      {options.map((option) => (
        <button
          key={option.value}
          type="button"
          aria-pressed={value === option.value}
          onClick={() => onChange(option.value)}
          className={cn("rounded-md px-3 py-1 font-medium", value === option.value ? "bg-sunken text-ink" : "text-ink-2 hover:text-ink")}
        >
          {option.label}
        </button>
      ))}
    </div>
  );
}
