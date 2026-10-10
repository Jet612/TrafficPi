import { dayText, formatCount, localDate, momentText, timeText, yearIn } from "./format";
import { sizeText } from "./settings";
import type { AcceleratorStatus, BackupInfo, DataUsage, MemoryTest } from "./types";

/** How the status page names the chip: HAILO8L is the Hailo-8L, the 13 TOPS one on the AI HAT+. */
export function chipName(architecture: string | null | undefined): string {
  if (!architecture) return "unknown";
  const match = /^HAILO(\d+)(L?)$/i.exec(architecture);
  if (!match) return architecture;
  const [, generation = "", light = ""] = match;
  return `Hailo-${generation}${light.toUpperCase()}`;
}

/** What else is known of the chip, as facts to list under its name: the model it runs and its firmware. Nothing it does not know. */
export function acceleratorFacts(accelerator: AcceleratorStatus): { label: string; value: string }[] {
  const facts: { label: string; value: string }[] = [];
  if (accelerator.model) facts.push({ label: "Model", value: accelerator.model });
  if (accelerator.firmware) facts.push({ label: "Firmware", value: accelerator.firmware });
  return facts;
}

/** How long a picture takes: "17.4 ms" and "95% under 17.9 ms"; null when it has not been timed. */
export function latencyText(accelerator: AcceleratorStatus): { value: string; note: string | null } | null {
  if (accelerator.inference_ms == null) return null;
  return {
    value: `${accelerator.inference_ms.toFixed(1)} ms`,
    note: accelerator.inference_p95_ms == null ? null : `95% under ${accelerator.inference_p95_ms.toFixed(1)} ms`,
  };
}

/** The three sizes the data directory is made of, and how many counts are stored, as facts to list: "Database 0.4 MB". */
export function dataFacts(usage: DataUsage, countsStored: number): { label: string; value: string }[] {
  const mb = (n: number) => sizeText(n < 10 ? Math.round(n * 10) / 10 : Math.round(n));
  return [
    { label: "Database", value: mb(usage.database_mb) },
    { label: "Pictures and clips", value: `${mb(usage.evidence_mb)} (${usage.evidence_files} ${usage.evidence_files === 1 ? "file" : "files"})` },
    { label: "Backups", value: `${mb(usage.backups_mb)} (${usage.backup_files})` },
    { label: "Counts stored", value: formatCount(countsStored) },
  ];
}

/** A nightly backup that is more than a day and a half old has been missed. */
export const BACKUP_STALE_MS = 36 * 3600 * 1000;

export type BackupLevel = "good" | "warning" | "none";

export function backupLevel(backup: BackupInfo | null | undefined, nowMs: number): BackupLevel {
  if (!backup) return "none";
  return nowMs - backup.at_ms > BACKUP_STALE_MS ? "warning" : "good";
}

/** When the last backup was ("Yesterday, 03:30"), and how big and how many: "0.4 MB · 7 kept"; or that there is none. */
export function backupText(backup: BackupInfo | null | undefined, timeZone: string, nowMs: number): { value: string; note: string } {
  if (!backup) return { value: "none yet", note: "no daily backup has been made (scripts/install_services.sh --backup-at)" };
  return { value: momentText(backup.at_ms, timeZone, nowMs), note: `${sizeText(Math.round(backup.size_mb * 10) / 10)} · ${backup.count} kept` };
}

/** What the installer's memory test said, in words. */
export function memoryTestText(test: MemoryTest | null | undefined, timeZone: string, nowMs: number): { value: string; note: string; ok: boolean | null } {
  if (!test) return { value: "not run", note: "the installer runs a quick one; none is on record here", ok: null };
  const date = localDate(test.at_ms, timeZone);
  const when = `${dayText(date, yearIn(nowMs, timeZone))}, ${timeText(test.at_ms, timeZone)}`;
  return test.ok
    ? { value: "passed", note: `${test.mb} MB tested once, ${when}`, ok: true }
    : { value: "problem found", note: `${test.mb} MB tested, ${when}: other results from this board cannot be trusted`, ok: false };
}
