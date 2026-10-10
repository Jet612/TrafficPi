import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/cn";

/**
 * The longer explanation of a card, kept behind one line ("How this works") so that the card opens
 * with a sentence and not a paragraph. It is the browser's own <details>: closed it costs a line,
 * and its words are still in the page for anyone who searches it or reads it with a screen reader.
 */
export function HowItWorks({ title = "How this works", className, children }: { title?: string; className?: string; children: React.ReactNode }) {
  return (
    <details className={cn("group max-w-3xl text-sm", className)}>
      <summary className="inline-flex cursor-pointer list-none items-center gap-1 rounded-md py-0.5 font-medium text-accent [&::-webkit-details-marker]:hidden">
        <ChevronRight className="size-4 transition-transform group-open:rotate-90" aria-hidden="true" />
        {title}
      </summary>
      <div className="mt-1.5 grid gap-2 border-l-2 border-edge pl-4 text-ink-2">{children}</div>
    </details>
  );
}
