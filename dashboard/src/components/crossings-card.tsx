"use client";

import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { DirectionArrow } from "@/components/ui/direction-arrow";
import { useLiveSelector } from "@/components/live-provider";
import { useStats } from "@/components/stats-provider";
import { crossingHeading } from "@/lib/direction";
import { formatCount } from "@/lib/format";
import { sameLines, selectLines } from "@/lib/live-select";

/**
 * What each counting line has counted today and which way the vehicles went. It sits under the live
 * picture because it describes the lines drawn on it; the arrow beside a number is the one on that
 * line in the picture.
 */
export function CrossingsCard() {
  const { today } = useStats();
  const lines = useLiveSelector(selectLines, sameLines);
  if (!today || today.lines.length === 0) return null;
  const geometry = new Map((lines ?? []).map((line) => [line.id, line]));
  const forward = today.lines.reduce((sum, line) => sum + line.forward, 0);
  const reverse = today.lines.reduce((sum, line) => sum + line.reverse, 0);
  // A total is for adding up several rows; with one line and nothing removed it would repeat the row.
  const showTotal = today.lines.length > 1 || today.removed > 0;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Crossings by counting line</CardTitle>
          <CardSubtitle>Forward is the way the arrow on the video points.</CardSubtitle>
        </div>
      </CardHeader>
      <CardBody>
        <table className="w-full text-sm">
          <caption className="sr-only">Crossings by counting line</caption>
          <thead>
            <tr className="text-left text-ink-2">
              <th scope="col" className="pb-1.5 pr-3 font-medium">Line</th>
              <th scope="col" className="whitespace-nowrap pb-1.5 pr-3 text-right font-medium">Forward</th>
              <th scope="col" className="whitespace-nowrap pb-1.5 pr-3 text-right font-medium">Reverse</th>
              <th scope="col" className="whitespace-nowrap pb-1.5 text-right font-medium">Both ways</th>
            </tr>
          </thead>
          <tbody>
            {today.lines.map((line) => {
              const shape = geometry.get(line.id);
              return (
                <tr key={line.id} className="border-t border-grid">
                  <th scope="row" className="py-1.5 pr-3 text-left font-normal text-ink">{line.name || line.id}</th>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink">
                    <span className="inline-flex items-center justify-end gap-1.5">
                      {shape && <DirectionArrow heading={crossingHeading(shape, true)} className="text-ink-3" />}
                      {formatCount(line.forward)}
                    </span>
                  </td>
                  <td className="py-1.5 pr-3 text-right tabular-nums text-ink">
                    <span className="inline-flex items-center justify-end gap-1.5">
                      {shape && <DirectionArrow heading={crossingHeading(shape, false)} className="text-ink-3" />}
                      {formatCount(line.reverse)}
                    </span>
                  </td>
                  <td className="py-1.5 text-right tabular-nums text-ink">{formatCount(line.forward + line.reverse)}</td>
                </tr>
              );
            })}
            {today.removed > 0 && (
              <tr className="border-t border-grid text-ink-3" data-testid="removed-lines-note">
                <th scope="row" className="py-1.5 pr-3 text-left font-normal">Removed lines</th>
                <td className="py-1.5 pr-3 text-right">–</td>
                <td className="py-1.5 pr-3 text-right">–</td>
                <td className="py-1.5 text-right tabular-nums">{formatCount(today.removed)}</td>
              </tr>
            )}
          </tbody>
          {showTotal && (
            <tfoot>
              <tr className="border-t border-baseline font-medium">
                <th scope="row" className="py-1.5 pr-3 text-left">Total</th>
                <td className="py-1.5 pr-3 text-right tabular-nums text-ink">{formatCount(forward)}</td>
                <td className="py-1.5 pr-3 text-right tabular-nums text-ink">{formatCount(reverse)}</td>
                <td className="py-1.5 text-right tabular-nums text-ink">{formatCount(today.total)}</td>
              </tr>
            </tfoot>
          )}
        </table>
      </CardBody>
    </Card>
  );
}
