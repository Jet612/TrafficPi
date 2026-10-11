/**
 * The speeds of the vehicles counted, as the Data page shows them: rows for the
 * histogram, and words for the figures. The sums are the server's (`GET /api/stats/speeds`), in the
 * owner's units; a speed measured on a calibrated road is shown to the whole mile or kilometre an
 * hour, without an "about" (`measuredSpeed`).
 */
import type { ChartRow, ChartSeries } from "./data";
import { measuredSpeed, unitName, type SpeedUnits } from "./speed";
import type { SpeedStats } from "./types";

/** The histogram has one series: how many vehicles were in the bar. */
export const SPEED_SERIES: readonly ChartSeries[] = [{ key: "vehicles", label: "Vehicles", color: "var(--accent)" }];

/** One column of the histogram for each bar: the axis says where it begins, the tooltip and the table say both ends. */
export function speedBinRows(bins: SpeedStats["bins"], units: SpeedUnits): ChartRow[] {
  return bins.map((bin) => ({
    id: `bin-${bin.low}`,
    label: String(bin.low),
    title: `${bin.low} to ${bin.high} ${unitName(units)}`,
    values: { vehicles: bin.count },
    total: bin.count,
  }));
}

/** A speed from the server (in the units already) as a whole number with its unit: "28 mph". */
export const speedValue = (value: number, units: SpeedUnits): string => `${measuredSpeed(value)} ${unitName(units)}`;

/** How many of the vehicles counted have a speed: the rest were not seen for long enough, or not where the calibration is believed. */
export function coverageText(stats: Pick<SpeedStats, "counted" | "figures">): string {
  const measured = stats.figures?.count ?? 0;
  if (stats.counted === 0) return measured === 0 ? "No vehicle was counted." : `${measured} had a speed.`;
  if (measured === 0) return `None of the ${stats.counted} vehicles counted had a speed.`;
  const share = Math.round((measured / stats.counted) * 100);
  return `${measured} of the ${stats.counted} vehicles counted had a speed (${share}%).`;
}
