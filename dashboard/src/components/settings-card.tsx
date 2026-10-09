"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import { StatusBadge } from "@/components/ui/status";
import { Switch } from "@/components/ui/switch";
import { useSession } from "@/components/session";
import { useUnsavedWarning } from "@/components/unsaved";
import { ApiError, api } from "@/lib/api";
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
  const daysId = useId();
  const daysHelpId = useId();
  const evidenceDaysId = useId();
  const evidenceDaysHelpId = useId();
  const evidenceMbId = useId();
  const evidenceMbHelpId = useId();
  const rollingHoursId = useId();
  const rollingHoursHelpId = useId();
  const didNotStopHoursId = useId();
  const didNotStopHoursHelpId = useId();

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
        <CardTitle>Settings</CardTitle>
      </CardHeader>
      <CardBody>
        {draft === null ? (
          <p className="text-sm text-ink-2">{loadFailed ? "The settings could not be loaded." : "Loading…"}</p>
        ) : (
          <form
            aria-label="Settings"
            className="grid gap-x-10 gap-y-6 md:grid-cols-2"
            onSubmit={(event) => {
              event.preventDefault();
              void save();
            }}
          >
            <div className="grid content-start gap-5">
              <div>
                <Label htmlFor={zoneId}>Time zone</Label>
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
                <p id={zoneHelpId} className="mt-1.5 text-sm text-ink-2">
                  The zone of the camera, like America/New_York. Days, hours and &ldquo;today&rdquo; are counted by its clock.
                  {here !== null && here !== draft.timezone.trim() && (
                    <>
                      {" "}
                      <button type="button" className="font-medium text-accent underline underline-offset-2" onClick={() => change({ timezone: here })}>
                        Use this browser&rsquo;s zone, {here}
                      </button>
                    </>
                  )}
                </p>
              </div>

              <div>
                <p className="mb-1.5 text-sm font-medium text-ink">Speed units</p>
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
                <p className="mt-1.5 text-sm text-ink-2">For speeds, once they are measured (a later step). Nothing shows them yet.</p>
              </div>

              <fieldset className="grid content-start gap-3">
                <legend className="mb-1 text-sm font-medium text-ink">What the camera looks for</legend>
                <p className="text-sm text-ink-2">
                  Vehicles are always looked for. With people switched off, nobody who walks past gets a box in the picture, a path or a count, and a counting line that counts people stays
                  silent. The change applies at once.
                </p>
                <Switch label="Look for people" checked={draft.detectPeople} onChange={(detectPeople) => change({ detectPeople })} />
              </fieldset>

              <fieldset className="grid content-start gap-3">
                <legend className="mb-1 text-sm font-medium text-ink">Trails</legend>
                <p className="text-sm text-ink-2">
                  A trail is the path a vehicle took across the picture, saved when it leaves it: no picture of the vehicle, no number plate, no face.
                </p>
                <Switch label="Save the path of each vehicle" checked={draft.trailsEnabled} onChange={(trailsEnabled) => change({ trailsEnabled })} />
                <Switch label="Include people" checked={draft.trailsPeople} disabled={!draft.detectPeople} onChange={(trailsPeople) => change({ trailsPeople })} />
                {!draft.detectPeople && <p className="text-sm text-ink-2">People are not being looked for, so there are no paths of people to save.</p>}
                <div>
                  <Label htmlFor={daysId}>Keep trails for (days)</Label>
                  <Input
                    id={daysId}
                    inputMode="numeric"
                    autoComplete="off"
                    value={draft.keepDays}
                    onChange={(event) => change({ keepDays: event.target.value })}
                    aria-invalid={days === null}
                    aria-describedby={daysHelpId}
                    className="w-28"
                  />
                  <p id={daysHelpId} className="mt-1.5 text-sm text-ink-2">
                    {days === null ? `A whole number of days, from 1 to ${MAX_KEEP_DAYS}.` : `Kept for ${roughly(days)}. Older trails are deleted by themselves.`}
                  </p>
                </div>
              </fieldset>

              <fieldset className="grid content-start gap-3">
                <legend className="mb-1 text-sm font-medium text-ink">Viewers who come through Cloudflare</legend>
                <p className="text-sm text-ink-2">
                  If you reach this dashboard from outside through a Cloudflare Tunnel, the numbers, the charts, the pictures and the boxes work as usual, but the
                  continuous live video is held back and a still picture, renewed every few seconds, is shown instead. Cloudflare&rsquo;s terms let it limit video on its
                  network without a paid plan, and they do not say whether a Tunnel counts. People on your own network always get the live video.
                </p>
                <Switch
                  label="Allow live video for viewers who come through Cloudflare"
                  checked={draft.remoteLiveVideo}
                  onChange={(remoteLiveVideo) => change({ remoteLiveVideo })}
                />
                <p className="text-sm text-ink-2">
                  Read Cloudflare&rsquo;s terms first; docs/remote-access.md says where.
                  {remote && " You are looking at this page through Cloudflare now."}
                </p>
              </fieldset>
            </div>

            <fieldset className="grid content-start gap-3">
              <legend className="mb-1 text-sm font-medium text-ink">Evidence for stop signs</legend>
              <p className="text-sm text-ink-2">
                For each pass at a stop sign that needs a look (rolled through, did not stop, could not be told), a picture and a short clip are kept on this machine, for you to
                see and decide. A vehicle that stopped as it should gets none. The pictures are small, 640 by 360 pixels, so far-off number plates and faces cannot be read in them (one close to the camera might be).
              </p>
              <Switch
                label="Keep a picture and a clip of passes that need a look"
                checked={draft.evidenceEnabled}
                onChange={(evidenceEnabled) => change({ evidenceEnabled })}
              />
              <div className="grid gap-3 border-l-2 border-edge pl-4">
                <p className="text-sm text-ink-2">
                  Each kind of pass has a switch of its own. With one off, no picture and no clip are made of that kind of pass, but it is still judged and counted, on the Stop
                  signs card, on the Data page and in the daily email. What was kept already stays until its time is up. A pass that could not be told follows the switch above.
                </p>
                <Switch
                  label="Keep a picture and a clip of rolled-through stops"
                  checked={draft.evidenceKeepRolling}
                  disabled={!draft.evidenceEnabled}
                  onChange={(evidenceKeepRolling) => change({ evidenceKeepRolling })}
                />
                <Switch
                  label="Keep a picture and a clip of vehicles that did not stop"
                  checked={draft.evidenceKeepDidNotStop}
                  disabled={!draft.evidenceEnabled}
                  onChange={(evidenceKeepDidNotStop) => change({ evidenceKeepDidNotStop })}
                />
                {!draft.evidenceEnabled && <p className="text-sm text-ink-2">Nothing is kept while the switch above is off, whatever these say.</p>}
              </div>
              <div>
                <Label htmlFor={evidenceDaysId}>Keep pictures and clips for (days)</Label>
                <Input
                  id={evidenceDaysId}
                  inputMode="numeric"
                  autoComplete="off"
                  value={draft.evidenceDays}
                  onChange={(event) => change({ evidenceDays: event.target.value })}
                  aria-invalid={evidenceDays === null}
                  aria-describedby={evidenceDaysHelpId}
                  className="w-28"
                />
                <p id={evidenceDaysHelpId} className="mt-1.5 text-sm text-ink-2">
                  {evidenceDays === null
                    ? `A whole number of days, from 1 to ${MAX_KEEP_DAYS}.`
                    : `Kept for ${roughly(evidenceDays)}. Older ones are deleted by themselves, also if none are being made.`}
                </p>
              </div>
              <div>
                <Label htmlFor={rollingHoursId}>Keep the clip of a rolled-through stop for (hours)</Label>
                <Input
                  id={rollingHoursId}
                  inputMode="numeric"
                  autoComplete="off"
                  value={draft.rollingClipHours}
                  onChange={(event) => change({ rollingClipHours: event.target.value })}
                  aria-invalid={rollingHours === null}
                  aria-describedby={rollingHoursHelpId}
                  className="w-28"
                />
                <p id={rollingHoursHelpId} className="mt-1.5 text-sm text-ink-2">
                  {rollingHours === null
                    ? `A whole number of hours, from 1 to ${MAX_CLIP_HOURS}.`
                    : `Clips of rolled-through stops are deleted after ${hoursText(rollingHours)}, to spare the disk. The picture stays for the days above, and the count for good.`}
                </p>
              </div>
              <div>
                <Label htmlFor={didNotStopHoursId}>Keep the clip of a vehicle that did not stop for (hours)</Label>
                <Input
                  id={didNotStopHoursId}
                  inputMode="numeric"
                  autoComplete="off"
                  value={draft.didNotStopClipHours}
                  onChange={(event) => change({ didNotStopClipHours: event.target.value })}
                  aria-invalid={didNotStopHours === null}
                  aria-describedby={didNotStopHoursHelpId}
                  className="w-28"
                />
                <p id={didNotStopHoursHelpId} className="mt-1.5 text-sm text-ink-2">
                  {didNotStopHours === null
                    ? `A whole number of hours, from 1 to ${MAX_CLIP_HOURS}.`
                    : `Clips of vehicles that did not stop are deleted after ${hoursText(didNotStopHours)}. Neither can be kept longer than the days above.`}
                </p>
              </div>
              <div>
                <Label htmlFor={evidenceMbId}>Keep at most (megabytes)</Label>
                <Input
                  id={evidenceMbId}
                  inputMode="numeric"
                  autoComplete="off"
                  value={draft.evidenceMb}
                  onChange={(event) => change({ evidenceMb: event.target.value })}
                  aria-invalid={evidenceMb === null}
                  aria-describedby={evidenceMbHelpId}
                  className="w-28"
                />
                <p id={evidenceMbHelpId} className="mt-1.5 text-sm text-ink-2">
                  {evidenceMb === null
                    ? `A whole number of megabytes, from ${MIN_EVIDENCE_MB} to ${MAX_EVIDENCE_MB}.`
                    : `Up to ${sizeText(evidenceMb)} on the disk. When there is more, the oldest are deleted first.`}
                </p>
              </div>
            </fieldset>

            <div className="flex flex-wrap items-center gap-3 border-t border-edge pt-4 md:col-span-2">
              <Button type="submit" variant="primary" busy={busy} disabled={!dirty || problem !== null || busy}>
                Save settings
              </Button>
              {problem !== null ? (
                <StatusBadge level="critical">{problem}</StatusBadge>
              ) : (
                dirty && <StatusBadge level="warning">Settings not saved yet</StatusBadge>
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
