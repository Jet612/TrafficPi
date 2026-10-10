import { Fragment } from "react";
import { Sunrise, Sunset } from "lucide-react";
import { cn } from "@/lib/cn";
import { DAY_MINUTES, clockText, segmentsText, type Segment, type Timeline } from "@/lib/stop-hours";

const percent = (minutes: number) => `${(minutes / DAY_MINUTES) * 100}%`;

/** One thin track for the 24 hours, with its stretches drawn on it. */
function Track({ segments, solid = false, tall = false, children }: { segments: readonly Segment[]; solid?: boolean; tall?: boolean; children?: React.ReactNode }) {
  return (
    <div className={cn("relative rounded-full bg-sunken", tall ? "h-5" : "h-3")}>
      {segments.map((one) => (
        <span
          key={`${one.from}-${one.to}`}
          className={cn("absolute top-0 h-full rounded-full", solid ? "bg-accent" : "bg-accent/35")}
          style={{ left: percent(one.from), width: percent(one.to - one.from) }}
        />
      ))}
      {children}
    </div>
  );
}

function SunMarker({ at, label, children }: { at: number; label: string; children: React.ReactNode }) {
  return (
    <span className="absolute top-1/2 -translate-x-1/2 -translate-y-1/2 text-ink" style={{ left: percent(at) }} title={`${label} ${clockText(at)}`}>
      {children}
    </span>
  );
}

/**
 * The hours stop signs are judged, as one day laid out from midnight to midnight: where the daylight
 * window is (with sunrise and sunset), where the set times are, the stretches that are judged because
 * they are inside every limit that is on, and where now is. The sentence beside it in the card says the
 * same in words, and so does the label of the picture.
 */
export function HoursTimeline({ line }: { line: Timeline }) {
  const summary = [
    line.daylight !== null ? `Daylight ${segmentsText(line.daylight)}.` : null,
    line.setTimes !== null ? `Set times ${segmentsText(line.setTimes)}.` : null,
    `Judged ${segmentsText(line.judged)}.`,
    `Now ${clockText(line.now)}.`,
  ]
    .filter(Boolean)
    .join(" ");
  return (
    <div role="img" aria-label={`Today's hours for judging stop signs. ${summary}`} data-testid="hours-timeline" className="relative">
      <div className="grid grid-cols-[5.5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs text-ink-2">
        {line.daylight !== null && (
          <Fragment>
            <span className="self-center">Daylight</span>
            <Track segments={line.daylight} tall>
              {line.sunrise !== null && (
                <SunMarker at={line.sunrise} label="Sunrise">
                  <Sunrise className="size-4" aria-hidden="true" />
                </SunMarker>
              )}
              {line.sunset !== null && (
                <SunMarker at={line.sunset} label="Sunset">
                  <Sunset className="size-4" aria-hidden="true" />
                </SunMarker>
              )}
            </Track>
          </Fragment>
        )}
        {line.setTimes !== null && (
          <Fragment>
            <span className="self-center">Set times</span>
            <Track segments={line.setTimes} />
          </Fragment>
        )}
        <span className="self-center font-medium text-ink">Judged</span>
        <Track segments={line.judged} solid />
        <span aria-hidden="true" />
        <div className="relative h-4 text-ink-3" aria-hidden="true">
          {[0, 6, 12, 18, 24].map((hour) => (
            <span
              key={hour}
              className={cn("absolute top-0 tabular-nums", hour === 24 ? "-translate-x-full" : hour === 0 ? "" : "-translate-x-1/2")}
              style={{ left: percent(hour * 60) }}
            >
              {String(hour).padStart(2, "0")}
            </span>
          ))}
        </div>
      </div>
      {/* Where now is, drawn over every row: from the tracks' left edge (the label column and its gap) to the right. */}
      <div className="pointer-events-none absolute bottom-6 left-[calc(5.5rem+0.75rem)] right-0 top-0" aria-hidden="true">
        <span className="absolute inset-y-0 w-0.5 -translate-x-1/2 rounded-full bg-ink" style={{ left: percent(line.now) }} title={`Now ${clockText(line.now)}`} />
      </div>
    </div>
  );
}
