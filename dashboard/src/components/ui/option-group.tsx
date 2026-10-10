import { useId } from "react";
import { InfoTip } from "@/components/ui/info-tip";
import { cn } from "@/lib/cn";

/**
 * A titled set of options (a fieldset). An (i) beside the title can explain the whole set. The set is
 * named by the title alone, not by the icon's name too (`aria-labelledby`).
 */
export function OptionGroup({
  title,
  hint,
  className,
  children,
}: {
  title: string;
  hint?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  const titleId = useId();
  return (
    <fieldset aria-labelledby={titleId} className={cn("grid content-start gap-3", className)}>
      <legend className="mb-1 text-sm font-medium text-ink">
        <span id={titleId}>{title}</span>
        {hint && <InfoTip className="-my-0.5 ml-1">{hint}</InfoTip>}
      </legend>
      {children}
    </fieldset>
  );
}
