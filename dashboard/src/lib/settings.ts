import type { Settings } from "./types";

/** The longest the trails or the evidence may be kept, in days: the server's own limit (backend core/trail_settings.py and core/evidence_settings.py). */
export const MAX_KEEP_DAYS = 3650;

/** The least and the most room that the evidence may be given, in megabytes: the server's own limits (backend core/evidence_settings.py). */
export const MIN_EVIDENCE_MB = 50;
export const MAX_EVIDENCE_MB = 1_000_000;

/** The most hours that a clip may be kept for: the server's own limit (backend core/evidence_settings.py). */
export const MAX_CLIP_HOURS = MAX_KEEP_DAYS * 24;

/** The settings as the form holds them: what is typed in a number box is text until it has been checked. */
export interface SettingsDraft {
  timezone: string;
  units: Settings["units"];
  trailsEnabled: boolean;
  trailsPeople: boolean;
  keepDays: string;
  evidenceEnabled: boolean;
  evidenceDays: string;
  evidenceMb: string;
  rollingClipHours: string;
  didNotStopClipHours: string;
  remoteLiveVideo: boolean;
}

export function toDraft(settings: Settings): SettingsDraft {
  return {
    timezone: settings.timezone,
    units: settings.units,
    trailsEnabled: settings.trails_enabled,
    trailsPeople: settings.trails_people,
    keepDays: String(settings.trails_keep_days),
    evidenceEnabled: settings.evidence_enabled,
    evidenceDays: String(settings.evidence_keep_days),
    evidenceMb: String(settings.evidence_max_mb),
    rollingClipHours: String(settings.evidence_rolling_clip_hours),
    didNotStopClipHours: String(settings.evidence_did_not_stop_clip_hours),
    remoteLiveVideo: settings.remote_live_video,
  };
}

/** The days typed, if they are a whole number the server will take; null otherwise. */
export function parseKeepDays(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d{1,5}$/.test(trimmed)) return null;
  const days = Number(trimmed);
  return days >= 1 && days <= MAX_KEEP_DAYS ? days : null;
}

/** The megabytes typed, if they are a whole number the server will take; null otherwise. */
export function parseEvidenceMb(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d{1,7}$/.test(trimmed)) return null;
  const mb = Number(trimmed);
  return mb >= MIN_EVIDENCE_MB && mb <= MAX_EVIDENCE_MB ? mb : null;
}

/** The hours typed, if they are a whole number the server will take; null otherwise. */
export function parseClipHours(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d{1,6}$/.test(trimmed)) return null;
  const hours = Number(trimmed);
  return hours >= 1 && hours <= MAX_CLIP_HOURS ? hours : null;
}

export type DraftResult = { ok: true; settings: Settings } | { ok: false; problem: string };

/** The settings to send, or what is wrong with the form. (The server checks that the time zone exists.) */
export function fromDraft(draft: SettingsDraft): DraftResult {
  const timezone = draft.timezone.trim();
  if (timezone === "") return { ok: false, problem: "Enter a time zone, like America/New_York." };
  const days = parseKeepDays(draft.keepDays);
  if (days === null) return { ok: false, problem: `Enter a whole number of days from 1 to ${MAX_KEEP_DAYS} to keep trails.` };
  const evidenceDays = parseKeepDays(draft.evidenceDays);
  if (evidenceDays === null) return { ok: false, problem: `Enter a whole number of days from 1 to ${MAX_KEEP_DAYS} to keep pictures and clips.` };
  const evidenceMb = parseEvidenceMb(draft.evidenceMb);
  if (evidenceMb === null) {
    return { ok: false, problem: `Enter a whole number of megabytes from ${MIN_EVIDENCE_MB} to ${MAX_EVIDENCE_MB} for pictures and clips.` };
  }
  const rollingHours = parseClipHours(draft.rollingClipHours);
  if (rollingHours === null) return { ok: false, problem: `Enter a whole number of hours from 1 to ${MAX_CLIP_HOURS} to keep the clips of rolled-through stops.` };
  const didNotStopHours = parseClipHours(draft.didNotStopClipHours);
  if (didNotStopHours === null) return { ok: false, problem: `Enter a whole number of hours from 1 to ${MAX_CLIP_HOURS} to keep the clips of vehicles that did not stop.` };
  return {
    ok: true,
    settings: {
      timezone,
      units: draft.units,
      trails_enabled: draft.trailsEnabled,
      trails_people: draft.trailsPeople,
      trails_keep_days: days,
      evidence_enabled: draft.evidenceEnabled,
      evidence_keep_days: evidenceDays,
      evidence_max_mb: evidenceMb,
      evidence_rolling_clip_hours: rollingHours,
      evidence_did_not_stop_clip_hours: didNotStopHours,
      remote_live_video: draft.remoteLiveVideo,
    },
  };
}

/** Whether the form differs from what is saved (spaces around the zone or the days do not count). */
export function isChanged(draft: SettingsDraft, saved: Settings): boolean {
  const result = fromDraft(draft);
  return !result.ok || JSON.stringify(sortedKeys(result.settings)) !== JSON.stringify(sortedKeys(saved));
}

function sortedKeys(settings: Settings): Record<string, unknown> {
  return Object.fromEntries(Object.entries(settings).sort(([a], [b]) => a.localeCompare(b)));
}

/** The time zones to suggest while typing: the browser's list, with UTC and the one in use, once each and in order. */
export function zoneChoices(supported: readonly string[], current: string): string[] {
  return [...new Set([...supported, "UTC", current].filter((name) => name !== ""))].sort((a, b) => a.localeCompare(b));
}

/** "500 MB", "2 GB", "1.5 GB": a number of megabytes (a thousand to the gigabyte, as the server counts them) at a glance. */
export function sizeText(mb: number): string {
  return mb < 1000 ? `${mb} MB` : `${Math.round(mb / 100) / 10} GB`;
}

/** "30 days", "about 6 months", "about 1 year", "about 2.5 years": a way to read a number of days at a glance. */
export function roughly(days: number): string {
  if (days < 60) return `${days} ${days === 1 ? "day" : "days"}`;
  if (days < 330) return `about ${Math.round(days / 30.4)} months`;
  const years = Math.round((days / 365) * 2) / 2;
  return `about ${years} ${years === 1 ? "year" : "years"}`;
}

/** "3 hours", "1 hour", "2 days", "50 hours": a number of hours at a glance (whole days are told as days). */
export function hoursText(hours: number): string {
  if (hours >= 48 && hours % 24 === 0) return roughly(hours / 24);
  return `${hours} ${hours === 1 ? "hour" : "hours"}`;
}
