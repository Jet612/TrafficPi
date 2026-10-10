/**
 * A clock time typed in 24 hours, tidied into "HH:MM": "9" is "09:00", "930" is "09:30", "9:30" is
 * "09:30" and "18" is "18:00". Null for anything that is not a time of day ("24:00", "9:75", "abc").
 * The browser's own time box is not used because it shows 12 hours ("07:00 AM") to anyone whose
 * language does, in a dashboard that writes every other time in 24.
 */
export function normalizeTime(text: string): string | null {
  const match = /^(\d{1,2})(?::?(\d{2}))?$/.exec(text.trim());
  if (match === null) return null;
  const digits = text.trim().replace(":", "");
  // "930" is 9:30 and "1830" is 18:30; a bare "18" is 18:00; "1 30" and the like do not match above.
  const hours = digits.length >= 3 ? Number(digits.slice(0, digits.length - 2)) : Number(match[1]);
  const minutes = digits.length >= 3 ? Number(digits.slice(-2)) : 0;
  if (!Number.isInteger(hours) || hours > 23 || minutes > 59) return null;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

/** Whether `text` is already a time of day in the canonical "HH:MM" form. */
export function isTime(text: string): boolean {
  return /^([01]\d|2[0-3]):[0-5]\d$/.test(text);
}
