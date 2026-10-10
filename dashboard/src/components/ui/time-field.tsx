"use client";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/cn";
import { isTime, normalizeTime } from "@/lib/time-field";

/**
 * A clock time in 24 hours ("07:00", "18:30"), typed. The browser's own time box shows "07:00 AM" to
 * anyone whose language does, in a dashboard that writes every other time in 24 hours. What is
 * typed is handed on as it is, so that a form can say what is wrong; leaving the box tidies it
 * ("9" becomes "09:00", "1830" becomes "18:30").
 */
export function TimeField({
  value,
  onChange,
  className,
  ...props
}: Omit<React.ComponentProps<"input">, "value" | "onChange" | "type"> & { value: string; onChange: (value: string) => void }) {
  return (
    <Input
      type="text"
      inputMode="numeric"
      autoComplete="off"
      spellCheck={false}
      placeholder="HH:MM"
      maxLength={5}
      value={value}
      aria-invalid={value !== "" && !isTime(value) ? true : undefined}
      onChange={(event) => onChange(event.target.value)}
      onBlur={() => {
        const tidy = normalizeTime(value);
        if (tidy !== null && tidy !== value) onChange(tidy);
      }}
      className={cn("w-24 tabular-nums", className)}
      {...props}
    />
  );
}
