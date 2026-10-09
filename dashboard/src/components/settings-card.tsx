"use client";

import { useEffect, useId, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Input, Label } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import { StatusBadge } from "@/components/ui/status";
import { Switch } from "@/components/ui/switch";
import { useUnsavedWarning } from "@/components/unsaved";
import { ApiError, api } from "@/lib/api";
import { MAX_KEEP_DAYS, fromDraft, isChanged, parseKeepDays, roughly, toDraft, zoneChoices, type SettingsDraft } from "@/lib/settings";
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
            </div>

            <fieldset className="grid content-start gap-3">
              <legend className="mb-1 text-sm font-medium text-ink">Trails</legend>
              <p className="text-sm text-ink-2">
                A trail is the path a vehicle took across the picture, saved when it leaves it: no picture of the vehicle, no number plate, no face.
              </p>
              <Switch label="Save the path of each vehicle" checked={draft.trailsEnabled} onChange={(trailsEnabled) => change({ trailsEnabled })} />
              <Switch label="Include people" checked={draft.trailsPeople} onChange={(trailsPeople) => change({ trailsPeople })} />
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
