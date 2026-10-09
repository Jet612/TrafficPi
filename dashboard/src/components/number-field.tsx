"use client";

import { useId, useState } from "react";
import { Input, Label } from "@/components/ui/input";

/**
 * A number that can be typed: what is in the box is kept as text while it is being typed (a "0." on
 * the way to "0.5" is not yet a number), applied as soon as it is one that is allowed, and put back
 * to the value in use when the box is left. `scale` shows a number in other units (0.08 as 8).
 */
export function NumberField({
  label,
  hint,
  value,
  min,
  max,
  step,
  scale = 1,
  unit,
  onChange,
  onCommit,
}: {
  label: string;
  hint?: string;
  value: number;
  /** The least and most allowed, in the same units as `value`. */
  min: number;
  max: number;
  step: number;
  scale?: number;
  unit?: string;
  /** A new allowed number, as typed: shown at once. */
  onChange: (value: number) => void;
  /** The box was left: one undo step. */
  onCommit: () => void;
}) {
  const id = useId();
  const hintId = useId();
  const shown = String(Math.round(value * scale * 1000) / 1000);
  // What is typed, while it is being typed; otherwise the box shows the value in use, which also
  // follows a change from elsewhere (undo, a reset).
  const [draft, setDraft] = useState<string | null>(null);
  const text = draft ?? shown;

  const typed = text.trim() === "" ? Number.NaN : Number(text);
  const allowed = Number.isFinite(typed) && typed >= min * scale && typed <= max * scale;

  return (
    <div>
      <Label htmlFor={id}>{label}</Label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          type="text"
          inputMode="decimal"
          autoComplete="off"
          value={text}
          step={step}
          aria-invalid={!allowed}
          aria-describedby={hintId}
          className="w-24"
          onChange={(event) => {
            setDraft(event.target.value);
            const next = event.target.value.trim() === "" ? Number.NaN : Number(event.target.value);
            if (Number.isFinite(next) && next >= min * scale && next <= max * scale) onChange(next / scale);
          }}
          onBlur={() => {
            setDraft(null);
            onCommit();
          }}
        />
        {unit && <span className="text-sm text-ink-2">{unit}</span>}
      </div>
      <p id={hintId} className={allowed ? "mt-1 text-sm text-ink-2" : "mt-1 text-sm text-critical"}>
        {allowed ? hint : `From ${min * scale} to ${max * scale}.`}
      </p>
    </div>
  );
}
