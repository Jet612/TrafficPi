import { Check } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * A checkbox shown as a chip, for choosing among several (which kinds to show). It is a real checkbox:
 * the box is hidden and the chip draws its state, with the wash and edge that every selected thing has
 * and a check. The space for the check is always there, so that turning a chip on does not move the
 * others.
 */
export function CheckChip({
  checked,
  onChange,
  className,
  children,
}: {
  checked: boolean;
  onChange: (checked: boolean) => void;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <label
      className={cn(
        "relative inline-flex h-8 cursor-pointer select-none items-center gap-1.5 rounded-lg border pl-2 pr-3 text-sm font-medium transition-colors pointer-coarse:h-10 has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent",
        checked ? "border-accent bg-accent-wash text-ink" : "border-edge bg-surface text-ink-2 hover:bg-sunken hover:text-ink",
        className,
      )}
    >
      <input type="checkbox" className="peer sr-only" checked={checked} onChange={(event) => onChange(event.target.checked)} />
      <span className="grid size-4 shrink-0 place-items-center" aria-hidden="true">
        {checked && <Check className="size-4" />}
      </span>
      {children}
    </label>
  );
}
