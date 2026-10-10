"use client";

import { cn } from "@/lib/cn";
import { WEEKDAYS, WEEKDAYS_SHORT, toggleDay } from "@/lib/mail";

/** One button for each day of the week, Monday first. `aria-pressed` says whether the day is picked. */
export function DayButtons({ days, onChange }: { days: readonly number[]; onChange: (days: number[]) => void }) {
  return (
    <div className="flex flex-wrap gap-2">
      {WEEKDAYS_SHORT.map((short, day) => {
        const on = days.includes(day);
        return (
          <button
            key={short}
            type="button"
            aria-pressed={on}
            aria-label={WEEKDAYS[day]}
            onClick={() => onChange(toggleDay(days, day))}
            className={cn(
              "h-9 min-w-12 rounded-lg border px-3 text-sm font-medium transition-colors",
              on ? "border-accent bg-accent text-accent-ink" : "border-baseline bg-surface text-ink-2 hover:bg-sunken",
            )}
          >
            {short}
          </button>
        );
      })}
    </div>
  );
}
