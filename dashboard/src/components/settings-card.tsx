"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { InfoTip } from "@/components/ui/info-tip";
import { Input, Label } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import { StatusBadge } from "@/components/ui/status";
import { Switch } from "@/components/ui/switch";
import { useSession } from "@/components/session";
import { useUnsavedWarning } from "@/components/unsaved";
import { ApiError, api } from "@/lib/api";
import { cn } from "@/lib/cn";
import {
  MAX_CLIP_HOURS,
  MAX_EVIDENCE_MB,
  MAX_KEEP_DAYS,
  MIN_EVIDENCE_MB,
  fromDraft,
  hoursText,
  isChanged,
  parseClipHours,
  parseEvidenceMb,
  parseKeepDays,
  roughly,
  sizeText,
  toDraft,
  zoneChoices,
  type SettingsDraft,
} from "@/lib/settings";
import type { Settings } from "@/lib/types";

type Outcome = { kind: "ok" | "error"; text: string } | null;

/** The zone this browser is in, and the names it knows: both need a browser, so both are read late. */
function browserZone(): string | null {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
  } catch {
    return null;
  }
}

function knownZones(): string[] {
  try {
    return Intl.supportedValuesOf("timeZone");
  } catch {
    return [];
  }
}

/** A heading for a group of settings that belong together, so that two columns of options are not one long run of labels. */
function Section({ title, hint, className, children }: { title: string; hint?: React.ReactNode; className?: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <div role="group" aria-labelledby={id} className={cn("grid content-start gap-4", className)}>
      <h3 id={id} className="flex items-center gap-1 border-b border-edge pb-2 text-base font-semibold text-ink">
        {title}
        {hint && <InfoTip className="-my-0.5">{hint}</InfoTip>}
      </h3>
      {children}
    </div>
  );
}

/**
 * A whole number with its unit after the box ("Keep trails for [30] days"), kept as text while it is typed.
 * What it does is in the bubble of its (i); what is wrong with it is told under it. Its accessible name
 * keeps the unit ("Keep trails for (days)"), so that a screen reader says what the box counts.
 */
function UnitField({
  label,
  name,
  unit,
  hint,
  value,
  onChange,
  problem,
  disabled = false,
  inline = false,
}: {
  /** What is said before the box. */
  label: string;
  /** What the box is called to a screen reader. */
  name: string;
  unit: string;
  hint: string;
  value: string;
  onChange: (text: string) => void;
  problem: string | null;
  disabled?: boolean;
  /** Without the label before the box, for a field that sits in a line of its own words. */
  inline?: boolean;
}) {
  const id = useId();
  const hintId = useId();
  const problemId = useId();
  return (
    <div className={cn(disabled && "opacity-60")}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        {!inline && (
          <label htmlFor={id} className="text-sm font-medium text-ink">
            {label}
          </label>
        )}
        <Input
          id={id}
          aria-label={name}
          inputMode="numeric"
          autoComplete="off"
          value={value}
          disabled={disabled}
          onChange={(event) => onChange(event.target.value)}
          aria-invalid={problem !== null}
          aria-describedby={problem === null ? hintId : `${hintId} ${problemId}`}
          className="w-24 tabular-nums"
        />
        <span className="text-sm text-ink-2">{unit}</span>
        <InfoTip id={hintId}>{hint}</InfoTip>
      </div>
      {problem !== null && (
        <p id={problemId} className="mt-1.5 text-sm text-critical">
          {problem}
        </p>
      )}
    </div>
  );
}

export function SettingsCard() {
  const { remote, recheck } = useSession();
  const [saved, setSaved] = useState<Settings | null>(null);
  const [draft, setDraft] = useState<SettingsDraft | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const zoneId = useId();
  const zoneListId = useId();
  const zoneHelpId = useId();

  useEffect(() => {
    const controller = new AbortController();
    api
      .settings(controller.signal)
      .then((settings) => {
        setSaved(settings);
        setDraft(toDraft(settings));
      })
      .catch((error: unknown) => {
        if (!controller.signal.aborted) {
          console.error("could not load the settings", error);
          setLoadFailed(true);
        }
      });
    return () => controller.abort();
  }, []);

  const dirty = saved !== null && draft !== null && isChanged(draft, saved);
  useUnsavedWarning(dirty);
  const checked = draft ? fromDraft(draft) : null;
  const problem = checked && !checked.ok ? checked.problem : null;
  const zones = useMemo(() => (draft ? zoneChoices(knownZones(), draft.timezone.trim()) : []), [draft]);
  const here = draft ? browserZone() : null;
  const days = draft ? parseKeepDays(draft.keepDays) : null;
  const evidenceDays = draft ? parseKeepDays(draft.evidenceDays) : null;
  const evidenceMb = draft ? parseEvidenceMb(draft.evidenceMb) : null;
  const rollingHours = draft ? parseClipHours(draft.rollingClipHours) : null;
  const didNotStopHours = draft ? parseClipHours(draft.didNotStopClipHours) : null;

  function change(update: Partial<SettingsDraft>) {
    setOutcome(null);
    setDraft((current) => (current ? { ...current, ...update } : current));
  }

  async function save() {
    if (!checked || !checked.ok) return;
    setBusy(true);
    setOutcome(null);
    try {
      const stored = await api.saveSettings(checked.settings);
      setSaved(stored);
      setDraft(toDraft(stored));
      setOutcome({ kind: "ok", text: "Saved." });
      recheck(); // whether the live view may show video can have changed with this
    } catch (error) {
      setOutcome({ kind: "error", text: error instanceof ApiError ? error.message : "Could not save." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>General &amp; storage</CardTitle>
          <CardSubtitle>The camera&rsquo;s time zone, units and detection, and what is saved and for how long.</CardSubtitle>
        </div>
      </CardHeader>
      <CardBody>
        {draft === null ? (
          <p className="text-sm text-ink-2">{loadFailed ? "The settings could not be loaded." : "Loading…"}</p>
        ) : (
          <form
            aria-label="Settings"
            className="grid gap-x-10 gap-y-8 md:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <div className="grid content-start gap-8">
              <Section title="General">
                <div>
                  <Label
                    htmlFor={zoneId}
                    hintId={zoneHelpId}
                    hint={<>The zone of the camera, like America/New_York. Days, hours and &ldquo;today&rdquo; are counted by its clock.</>}
                  >
                    Time zone
                  </Label>
                  <Input
                    id={zoneId}
                    list={zoneListId}
                    value={draft.timezone}
                    onChange={(event) => change({ timezone: event.target.value })}
                    autoComplete="off"
                    spellCheck={false}
                    aria-describedby={zoneHelpId}
                    className="max-w-sm"
                  />
                  <datalist id={zoneListId}>
                    {zones.map((name) => (
                      <option key={name} value={name} />
                    ))}
                  </datalist>
                  {here !== null && here !== draft.timezone.trim() && (
                    <p className="mt-1.5 text-sm text-ink-2">
                      <button type="button" className="font-medium text-accent underline underline-offset-2" onClick={() => change({ timezone: here })}>
                        Use this browser&rsquo;s zone, {here}
                      </button>
                    </p>
                  )}
                </div>

                <div>
                  <div className="mb-1.5 flex items-center gap-1">
                    <p className="text-sm font-medium text-ink">Speed units</p>
                    <InfoTip className="-my-0.5">What the estimated speeds on the History and Data pages and in the daily email are told in.</InfoTip>
                  </div>
                  <Segmented
                    className="w-fit"
                    label="Speed units"
                    value={draft.units}
                    options={[
                      { value: "mph", label: "mph" },
                      { value: "kmh", label: "km/h" },
                    ]}
                    onChange={(units) => change({ units })}
                  />
                </div>
              </Section>

              <Section title="Detection">
                <Switch
                  label="Look for people"
                  hint="Vehicles are always looked for. With people switched off, nobody who walks past gets a box in the picture, a path or a count, and a counting line that counts people stays silent. The change applies at once."
                  checked={draft.detectPeople}
                  onChange={(detectPeople) => change({ detectPeople })}
                />
              </Section>

              <Section title="Trails" hint="A trail is the path a vehicle took across the picture, saved when it leaves it: no picture of the vehicle, no number plate, no face.">
                <Switch label="Save the path of each vehicle" checked={draft.trailsEnabled} onChange={(trailsEnabled) => change({ trailsEnabled })} />
                <Switch label="Include people" checked={draft.trailsPeople} disabled={!draft.detectPeople} onChange={(trailsPeople) => change({ trailsPeople })} />
                {!draft.detectPeople && <p className="text-sm text-ink-2">People are not being looked for, so there are no paths of people to save.</p>}
                <UnitField
                  label="Keep trails for"
                  name="Keep trails for (days)"
                  unit="days"
                  hint={`${days === null ? "" : `Kept for ${roughly(days)}. `}Older trails are deleted by themselves.`}
                  value={draft.keepDays}
                  onChange={(text) => change({ keepDays: text })}
                  problem={days === null ? `A whole number of days, from 1 to ${MAX_KEEP_DAYS}.` : null}
                />
              </Section>

              <Section
                title="Remote access"
                hint="If you reach this dashboard from outside through a Cloudflare Tunnel, the numbers, the charts, the pictures and the boxes work as usual, but the continuous live video is held back and a still picture, renewed every few seconds, is shown instead. Cloudflare's terms let it limit video on its network without a paid plan, and they do not say whether a Tunnel counts. People on your own network always get the live video. Read Cloudflare's terms first; docs/remote-access.md says where."
              >
                <Switch
                  label="Allow live video for viewers who come through Cloudflare"
                  checked={draft.remoteLiveVideo}
                  onChange={(remoteLiveVideo) => change({ remoteLiveVideo })}
                />
                {remote && <p className="text-sm text-ink-2">You are looking at this page through Cloudflare now.</p>}
              </Section>
            </div>

            <Section
              title="Evidence & storage"
              hint="For each pass at a stop sign that needs review (rolled through, did not stop, unclear), a picture and a short clip are kept on this machine. A vehicle that stopped as it should gets none. The pictures are small, 640 by 360 pixels, so far-off number plates and faces cannot be read in them (one close to the camera might be)."
            >
              <Switch
                label="Save evidence for stop-sign passes"
                hint="An unclear pass follows this switch alone. Rolled-through passes and passes that did not stop each have a switch of their own, below it."
                checked={draft.evidenceEnabled}
                onChange={(evidenceEnabled) => change({ evidenceEnabled })}
              />
              <div className="grid gap-3 border-l-2 border-edge pl-4">
                {!draft.evidenceEnabled && <p className="text-sm text-ink-2">Off: nothing is saved, whatever these say.</p>}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
                  <Switch
                    label="Rolled-through passes"
                    hint="With this off, no picture and no clip are made of a rolled-through pass. It is still judged and counted, on the Stop signs card, on the Data page and in the daily email. What was kept already stays until its time is up."
                    checked={draft.evidenceKeepRolling}
                    disabled={!draft.evidenceEnabled}
                    onChange={(evidenceKeepRolling) => change({ evidenceKeepRolling })}
                  />
                  <span className="inline-flex flex-wrap items-center gap-x-2 text-sm text-ink-2">
                    keep the clip for
                    <UnitField
                      inline
                      label="Keep the clip of rolled-through passes for"
                      name="Keep the clip of a rolled-through stop for (hours)"
                      unit="hours"
                      hint={`Clips of rolled-through passes are deleted after ${rollingHours === null ? "this time" : hoursText(rollingHours)}. The picture stays for the days below, and the count for good.`}
                      value={draft.rollingClipHours}
                      onChange={(text) => change({ rollingClipHours: text })}
                      problem={rollingHours === null ? `A whole number of hours, from 1 to ${MAX_CLIP_HOURS}.` : null}
                      disabled={!draft.evidenceEnabled || !draft.evidenceKeepRolling}
                    />
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5">
                  <Switch
                    label="Passes that did not stop"
                    hint="With this off, no picture and no clip are made of a vehicle that did not stop. It is still judged and counted, on the Stop signs card, on the Data page and in the daily email. What was kept already stays until its time is up."
                    checked={draft.evidenceKeepDidNotStop}
                    disabled={!draft.evidenceEnabled}
                    onChange={(evidenceKeepDidNotStop) => change({ evidenceKeepDidNotStop })}
                  />
                  <span className="inline-flex flex-wrap items-center gap-x-2 text-sm text-ink-2">
                    keep the clip for
                    <UnitField
                      inline
                      label="Keep the clip of passes that did not stop for"
                      name="Keep the clip of a vehicle that did not stop for (hours)"
                      unit="hours"
                      hint={`Clips of vehicles that did not stop are deleted after ${didNotStopHours === null ? "this time" : hoursText(didNotStopHours)}. Neither can be kept longer than the days below.`}
                      value={draft.didNotStopClipHours}
                      onChange={(text) => change({ didNotStopClipHours: text })}
                      problem={didNotStopHours === null ? `A whole number of hours, from 1 to ${MAX_CLIP_HOURS}.` : null}
                      disabled={!draft.evidenceEnabled || !draft.evidenceKeepDidNotStop}
                    />
                  </span>
                </div>
              </div>
              <UnitField
                label="Keep pictures and clips for"
                name="Keep pictures and clips for (days)"
                unit="days"
                hint={`${evidenceDays === null ? "" : `Kept for ${roughly(evidenceDays)}. `}Older ones are deleted by themselves, also if none are being made.`}
                value={draft.evidenceDays}
                onChange={(text) => change({ evidenceDays: text })}
                problem={evidenceDays === null ? `A whole number of days, from 1 to ${MAX_KEEP_DAYS}.` : null}
              />
              <UnitField
                label="Keep at most"
                name="Keep at most (megabytes)"
                unit="MB"
                hint={`Up to ${evidenceMb === null ? "this much" : sizeText(evidenceMb)} on the disk. When there is more, the oldest are deleted first.`}
                value={draft.evidenceMb}
                onChange={(text) => change({ evidenceMb: text })}
                problem={evidenceMb === null ? `A whole number of megabytes, from ${MIN_EVIDENCE_MB} to ${MAX_EVIDENCE_MB}.` : null}
              />
            </Section>

            <div className="flex flex-wrap items-center gap-3 border-t border-edge pt-4 md:col-span-2">
              <Button type="submit" variant="primary" busy={busy} disabled={!dirty || problem !== null || busy}>
                Save changes
              </Button>
              {problem !== null ? (
                <StatusBadge level="critical">{problem}</StatusBadge>
              ) : (
                dirty && <StatusBadge level="warning">Unsaved changes</StatusBadge>
              )}
              {outcome && (
                <p role={outcome.kind === "error" ? "alert" : "status"} className="text-sm text-ink">
                  <span
                    className={`mr-1.5 inline-block size-2 rounded-full align-middle ${outcome.kind === "error" ? "bg-critical" : "bg-good"}`}
                    aria-hidden="true"
                  />
                  <span className="font-medium">{outcome.kind === "error" ? "Not saved. " : ""}</span>
                  {outcome.text}
                </p>
              )}
            </div>
          </form>
        )}
      </CardBody>
    </Card>
  );
}
