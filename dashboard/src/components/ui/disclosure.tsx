import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * A group of fields or facts that most visits do not need, closed behind one line that says what is in
 * it. It is the browser's <details>: what is inside is still in the page (a screen reader finds it, a
 * search finds it), and `defaultOpen` opens it at first for a visit that does need it (a setting that is
 * still empty).
 */
export function Disclosure({
  title,
  status,
  defaultOpen = false,
  className,
  children,
}: {
  title: string;
  /** A word beside the title that says how the group stands, so that it need not be opened to know. */
  status?: React.ReactNode;
  defaultOpen?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <details open={defaultOpen} className={cn("group rounded-lg border border-edge", className)}>
      <summary className="flex cursor-pointer list-none items-center gap-2 rounded-lg px-3 py-2.5 text-sm font-medium text-ink [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 shrink-0 text-ink-3 transition-transform group-open:rotate-90" aria-hidden="true" />
        {title}
        {status && <span className="ml-auto">{status}</span>}
      </summary>
      <div className="grid gap-4 border-t border-edge px-3 py-3">{children}</div>
    </details>
  );
}
