import { formatCount } from "./format";

// The axis of a column chart: where it ends, how wide its labels are, where its gridlines go.

/** Round a chart's top up to a tidy value, so the axis ends on a labelled gridline. */
export function niceMax(value: number): number {
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 2.5, 4, 5, 10]) {
    const candidate = step * magnitude;
    if (candidate >= value) return candidate;
  }
  return 10 * magnitude;
}

/** Room in pixels for the widest label of a vertical axis that goes up to `max` (or to 100%): "25", "1,000" and "100%" each need their own. */
export function axisWidth(max: number, percent = false): number {
  const widest = percent ? "100%" : formatCount(max);
  return Math.max(36, Math.ceil(14 + widest.length * 7.5));
}

/** Gridline values from 0 to `max` (as returned by niceMax): whole numbers, four or five intervals. */
export function axisTicks(max: number): number[] {
  if (max <= 5) return Array.from({ length: Math.floor(max) + 1 }, (_, i) => i);
  for (let power = 1; power <= max; power *= 10) {
    for (const factor of [1, 2, 2.5, 5]) {
      const step = factor * power;
      if (Number.isInteger(step) && max % step === 0 && max / step <= 5) {
        return Array.from({ length: max / step + 1 }, (_, i) => i * step);
      }
    }
  }
  return [0, max];
}
