import { CircleCheck, CircleDashed, CircleX, TriangleAlert } from "lucide-react";
import { cn } from "@/lib/cn";

export type StatusLevel = "good" | "warning" | "critical" | "neutral";

const ICON = { good: CircleCheck, warning: TriangleAlert, critical: CircleX, neutral: CircleDashed } as const;
const COLOR = { good: "text-good", warning: "text-warning", critical: "text-critical", neutral: "text-ink-3" } as const;

/**
 * A state, always as an icon and words: the colour only backs them up, because status colours
 * (yellow especially) are too faint on a light page to carry meaning alone. The corners are
 * rounded, not a full pill, because the words may wrap (a pill that wraps turns into an egg); the
 * header's one-line pill asks for `rounded-full`.
 */
export function StatusBadge({ level, children, className }: { level: StatusLevel; children: React.ReactNode; className?: string }) {
  const Icon = ICON[level];
  return (
    <span className={cn("inline-flex items-start gap-1.5 rounded-lg border border-edge bg-sunken py-1 pl-2 pr-3 text-sm leading-5 text-ink", className)}>
      <Icon className={cn("mt-0.5 size-4 shrink-0", COLOR[level])} aria-hidden="true" />
      <span className="min-w-0">{children}</span>
    </span>
  );
}
