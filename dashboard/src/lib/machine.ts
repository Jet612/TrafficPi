import { formatDateTime } from "./format";
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

/** "firmware 4.23.0 · model yolov8s_h8l", or what is known of it. */
export function chipNote(accelerator: AcceleratorStatus): string {
  const parts = [
    accelerator.firmware ? `firmware ${accelerator.firmware}` : null,
    accelerator.model ? `model ${accelerator.model}` : null,
  ].filter((part): part is string => part !== null);
  return parts.join(" · ");
}

/** "17.4 ms a picture (95% under 17.9 ms)", or nothing when it has not been timed. */
export function inferenceText(accelerator: AcceleratorStatus): string | null {
  if (accelerator.inference_ms == null) return null;
  const mean = `${accelerator.inference_ms.toFixed(1)} ms a picture`;
  return accelerator.inference_p95_ms == null ? mean : `${mean} (95% under ${accelerator.inference_p95_ms.toFixed(1)} ms)`;
}

/** The three sizes in a line: "database 0.4 MB · pictures and clips 35 MB (112 files) · backups 3.2 MB (7)". */
export function dataText(usage: DataUsage): string {
  const mb = (n: number) => sizeText(n < 10 ? Math.round(n * 10) / 10 : Math.round(n));
  return [
    `database ${mb(usage.database_mb)}`,
    `pictures and clips ${mb(usage.evidence_mb)} (${usage.evidence_files} ${usage.evidence_files === 1 ? "file" : "files"})`,
    `backups ${mb(usage.backups_mb)} (${usage.backup_files})`,
  ].join(" · ");
}

/** A nightly backup that is more than a day and a half old has been missed. */
export const BACKUP_STALE_MS = 36 * 3600 * 1000;

export type BackupLevel = "good" | "warning" | "none";

export function backupLevel(backup: BackupInfo | null | undefined, nowMs: number): BackupLevel {
  if (!backup) return "none";
  return nowMs - backup.at_ms > BACKUP_STALE_MS ? "warning" : "good";
}

/** "2026-10-10 03:30", and how big and how many: "0.4 MB · 7 kept"; or that there is none. */
export function backupText(backup: BackupInfo | null | undefined, timeZone: string): { value: string; note: string } {
  if (!backup) return { value: "none yet", note: "no daily backup has been made (scripts/install_services.sh --backup-at)" };
  return { value: formatDateTime(backup.at_ms, timeZone), note: `${sizeText(Math.round(backup.size_mb * 10) / 10)} · ${backup.count} kept` };
}

/** What the installer's memory test said, in words. */
export function memoryTestText(test: MemoryTest | null | undefined, timeZone: string): { value: string; note: string; ok: boolean | null } {
  if (!test) return { value: "not run", note: "the installer runs a quick one; none is on record here", ok: null };
  const when = formatDateTime(test.at_ms, timeZone);
  return test.ok
    ? { value: "passed", note: `${test.mb} MB tested once, ${when}`, ok: true }
    : { value: "problem found", note: `${test.mb} MB tested, ${when}: other results from this board cannot be trusted`, ok: false };
}
