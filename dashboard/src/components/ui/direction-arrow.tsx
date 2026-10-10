import { ArrowDown, ArrowDownLeft, ArrowDownRight, ArrowLeft, ArrowRight, ArrowUp, ArrowUpLeft, ArrowUpRight, type LucideIcon } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Heading } from "@/lib/direction";

const ICON: Record<Heading, LucideIcon> = {
  up: ArrowUp,
  "up-right": ArrowUpRight,
  right: ArrowRight,
  "down-right": ArrowDownRight,
  down: ArrowDown,
  "down-left": ArrowDownLeft,
  left: ArrowLeft,
  "up-left": ArrowUpLeft,
};

/** An arrow pointing the way a vehicle crosses a line on the picture (decorative: the words say forward or reverse). */
export function DirectionArrow({ heading, className }: { heading: Heading; className?: string }) {
  const Icon = ICON[heading];
  return <Icon className={cn("size-4 shrink-0", className)} aria-hidden="true" />;
}
