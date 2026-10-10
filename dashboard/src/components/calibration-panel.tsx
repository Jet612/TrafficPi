"use client";

import { CircleAlert, CircleCheck, Plus, TriangleAlert } from "lucide-react";
import { NumberField } from "@/components/number-field";
import { Button } from "@/components/ui/button";
import {
  MAX_CHECKS,
  MAX_LENGTH_M,
  MAX_WIDTH_M,
  MIN_CHECK_M,
  MIN_LENGTH_M,
  MIN_WIDTH_M,
  checkText,
  lengthScale,
  lengthUnitName,
  lineProbeId,
  placeText,
  stopProbeId,
  type LengthUnit,
} from "@/lib/calibration";
import { addCheck, setCalibrationSize, setCheckLength } from "@/lib/editor";
import { displayName } from "@/lib/profile";
import type { ReportState } from "@/lib/use-calibration-report";
import type { CalibrationReport, CheckConfig, Profile } from "@/lib/types";

/** What the calibration panels need besides the profile: the server's report, and how lengths are shown. */
export interface CalibrationView {
  report: ReportState;
  unit: LengthUnit;
  onUnit: (unit: LengthUnit) => void;
  /** Whether the part of the picture where speeds are given is shaded. */
  shade: boolean;
  onShade: (shade: boolean) => void;
}

interface Edit {
  profile: Profile;
  onLive: (profile: Profile) => void;
  onSettle: () => void;
  onChange: (profile: Profile) => void;
  /** Select the element a change added. */
  onSelect: (ref: { kind: "check"; id: string }) => void;
}

const reportOf = (state: ReportState): CalibrationReport | null => ("report" in state ? state.report : null);

/** The rectangle's size, the unit lengths are shown in, and what the server makes of it all. */
export function CalibrationProperties({ view, ...edit }: Edit & { view: CalibrationView }) {
  const { profile, onLive, onSettle } = edit;
  const calibration = profile.calibration;
  if (calibration === null) return null;
  const scale = lengthScale(view.unit);
  const unit = view.unit;
  const report = reportOf(view.report);
  const full = calibration.checks.length >= MAX_CHECKS;

  return (
    <>
      <p className="mt-3 text-sm text-ink-2">
        Drag the four corners onto the corners of a rectangle on the road. The side from corner 1 to corner 2 is the width, across the road; the side from corner 2 to corner 3 is the length, along it.
      </p>

      <div className="mt-3 grid gap-3">
        <div>
          <label className="mb-1.5 block text-sm font-medium text-ink" htmlFor="calibration-unit">
            Lengths in
          </label>
          <select
            id="calibration-unit"
            value={unit}
            onChange={(event) => view.onUnit(event.target.value as LengthUnit)}
            className="h-10 rounded-lg border border-baseline bg-surface px-3 text-ink"
          >
            {(["m", "ft"] as const).map((name) => (
              <option key={name} value={name}>
                {lengthUnitName(name)}
              </option>
            ))}
          </select>
        </div>
        <NumberField
          label="Width, across the road"
          hint="The distance from corner 1 to corner 2."
          unit={unit}
          value={calibration.width_m}
          scale={scale}
          min={MIN_WIDTH_M}
          max={MAX_WIDTH_M}
          step={0.1}
          onChange={(value) => onLive(setCalibrationSize(profile, "width_m", value))}
          onCommit={onSettle}
        />
        <NumberField
          label="Length, along the road"
          hint="The distance from corner 2 to corner 3."
          unit={unit}
          value={calibration.length_m}
          scale={scale}
          min={MIN_LENGTH_M}
          max={MAX_LENGTH_M}
          step={0.1}
          onChange={(value) => onLive(setCalibrationSize(profile, "length_m", value))}
          onCommit={onSettle}
        />
      </div>

      <ReportSummary view={view} />

      <label className="mt-3 inline-flex min-h-8 cursor-pointer items-center gap-2 text-sm text-ink">
        <input type="checkbox" checked={view.shade} onChange={(event) => view.onShade(event.target.checked)} className="size-4 accent-[var(--accent)]" />
        Shade where speeds are given
      </label>

      {report !== null && <Places profile={profile} report={report} />}

      <div className="mt-3 flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={full}
          onClick={() => {
            const added = addCheck(profile);
            if (added === null) return;
            edit.onChange(added.profile);
            if (added.ref.kind === "check") edit.onSelect({ kind: "check", id: added.ref.id });
          }}
        >
          <Plus className="size-4" aria-hidden="true" />
          Add a known distance
        </Button>
        {full && <span className="self-center text-sm text-ink-2">At most {MAX_CHECKS}.</span>}
      </div>
    </>
  );
}

/** One sentence on how the server's report came out, or why there is none. */
function ReportSummary({ view }: { view: CalibrationView }) {
  const state = view.report;
  if (state.status === "none") return null;
  return (
    <div aria-live="polite" className="mt-3 text-sm">
      {state.status === "problem" ? (
        <p role="alert" className="flex items-start gap-1.5 text-ink">
          <CircleAlert className="mt-0.5 size-4 shrink-0 text-critical" aria-hidden="true" />
          <span>
            <span className="font-medium">This calibration cannot be used. </span>
            {state.problem}
          </span>
        </p>
      ) : state.status === "failed" && state.report === null ? (
        <p className="flex items-start gap-1.5 text-ink-2">
          <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
          <span>The camera service could not say what this calibration means. It is saved with the rest.</span>
        </p>
      ) : state.status === "loading" && state.report === null ? (
        <p className="text-ink-2">Working out where speeds can be given…</p>
      ) : (
        (() => {
          const report = reportOf(state);
          if (report === null) return null;
          return (
            <p className="text-ink-2">
              Speeds are given only where the calibration is expected to be right to within {Math.round(report.limit_pct)}%, in the shaded part of the picture.
              {report.centre_error_pct !== null && <> Near the middle of the rectangle it is expected to be right to about {Math.round(report.centre_error_pct)}%.</>}
              {state.status === "loading" && " Updating…"}
            </p>
          );
        })()
      )}
    </div>
  );
}

/** Where the stop lines and counting lines stand: whether speeds are given there. */
function Places({ profile, report }: { profile: Profile; report: CalibrationReport }) {
  const places = [
    ...profile.stop_signs.map((sign) => ({ id: stopProbeId(sign.id), name: displayName(profile, { kind: "stop", id: sign.id }), what: "stop line" })),
    ...profile.lines.map((line) => ({ id: lineProbeId(line.id), name: displayName(profile, { kind: "line", id: line.id }), what: "counting line" })),
  ];
  if (places.length === 0) return null;
  return (
    <ul aria-label="Where speeds are given" className="mt-3 grid gap-1.5 text-sm">
      {places.map((place) => {
        const said = placeText(report, place.id);
        if (said === null) return null;
        const Icon = said.believed ? CircleCheck : TriangleAlert;
        return (
          <li key={place.id} className="flex items-start gap-1.5 text-ink">
            <Icon className={`mt-0.5 size-4 shrink-0 ${said.believed ? "text-good" : "text-warning"}`} aria-hidden="true" />
            <span>
              <span className="font-medium">{place.name}</span> ({place.what}): {said.text}.
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** A known distance: how long it is, and what the calibrated map finds for it. */
export function CheckProperties({ view, check, ...edit }: Edit & { view: CalibrationView; check: CheckConfig }) {
  const { profile, onLive, onSettle } = edit;
  const scale = lengthScale(view.unit);
  const report = reportOf(view.report);
  const said = report === null ? null : checkText(report, check.id, view.unit);
  return (
    <>
      <div className="mt-3 grid gap-3">
        <NumberField
          label="Its length"
          hint="Drag the two ends onto two points of the road whose distance apart you know, and type it here. The map's own measure of it is shown below."
          unit={view.unit}
          value={check.length_m}
          scale={scale}
          min={MIN_CHECK_M}
          max={MAX_LENGTH_M}
          step={0.1}
          onChange={(value) => onLive(setCheckLength(profile, check.id, value))}
          onCommit={onSettle}
        />
      </div>
      <div aria-live="polite" className="mt-3 text-sm">
        {said === null ? (
          <p className="text-ink-2">{view.report.status === "problem" ? "The calibration cannot be used yet, so nothing is measured." : "Measuring…"}</p>
        ) : (
          <p className="flex items-start gap-1.5 text-ink">
            {said.agrees === false ? (
              <CircleAlert className="mt-0.5 size-4 shrink-0 text-critical" aria-hidden="true" />
            ) : said.agrees === true ? (
              <CircleCheck className="mt-0.5 size-4 shrink-0 text-good" aria-hidden="true" />
            ) : (
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden="true" />
            )}
            <span>
              {said.agrees === false ? <span className="font-medium">Does not agree: </span> : said.agrees === true ? <span className="font-medium">Agrees: </span> : null}
              {said.text}.
            </span>
          </p>
        )}
      </div>
    </>
  );
}
