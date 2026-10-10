import { CircleCheck, CircleDashed, CircleX, TriangleAlert, type LucideIcon } from "lucide-react";
import type { StopOutcome } from "@/lib/types";

/** The outcomes' keys: the status colours are reserved for them and are never alone (an icon and words go with each). */
export const OUTCOME_ICON: Record<StopOutcome, LucideIcon> = {
  stopped: CircleCheck,
  rolling: TriangleAlert,
  did_not_stop: CircleX,
  indeterminate: CircleDashed,
};
