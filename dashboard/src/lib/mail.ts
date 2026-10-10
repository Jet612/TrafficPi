import { atText, momentText, whenText } from "./format";
import type { MailRecipient, MailSchedule, MailSend, MailState } from "./types";

/** How a state is told: an icon and words (see ui/status.tsx), never a colour alone. */
export type Level = "good" | "warning" | "critical" | "neutral";

export const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] as const;
export const WEEKDAYS_SHORT = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"] as const;

/** The server's own limits (backend mail/settings.py): the form says so before it is asked. */
export const MAX_NAME = 60;
export const MAX_LINK = 300;

const TIME = /^([01]\d|2[0-3]):[0-5]\d$/;
const ADDRESS = /^[^\s@<>,;]+@[^\s@<>,;]+\.[^\s@<>,;]+$/;
const LINK = /^https?:\/\/[^\s<>"\\]+$/i;

/** "every day", "weekdays", "weekends", or the days by name: "Mon, Wed, Fri". */
export function daysText(days: readonly number[]): string {
  const sorted = [...days].sort((a, b) => a - b);
  const same = (other: number[]) => sorted.length === other.length && sorted.every((d, i) => d === other[i]);
  if (same([0, 1, 2, 3, 4, 5, 6])) return "every day";
  if (same([0, 1, 2, 3, 4])) return "weekdays";
  if (same([5, 6])) return "weekends";
  return sorted.map((d) => WEEKDAYS_SHORT[d] ?? "?").join(", ");
}

/** The schedule as the form holds it: what is typed is text until it has been checked. */
export interface ScheduleDraft {
  enabled: boolean;
  time: string;
  days: number[];
  senderName: string;
  senderAddress: string;
  linkUrl: string;
}

export function toScheduleDraft(schedule: MailSchedule): ScheduleDraft {
  return {
    enabled: schedule.enabled,
    time: schedule.time,
    days: [...schedule.days],
    senderName: schedule.sender_name,
    senderAddress: schedule.sender_address,
    linkUrl: schedule.link_url,
  };
}

export type DraftResult = { ok: true; schedule: MailSchedule } | { ok: false; problem: string };

/** The schedule to send, or what is wrong with the form, in words. (The server checks all of it again.) */
export function fromScheduleDraft(draft: ScheduleDraft): DraftResult {
  const time = draft.time.trim();
  if (!TIME.test(time)) return { ok: false, problem: "Enter the time as HH:MM, like 20:00." };
  if (draft.days.length === 0) return { ok: false, problem: "Pick at least one day of the week." };
  const name = draft.senderName.trim();
  if (name.length > MAX_NAME) return { ok: false, problem: `The sender name can have at most ${MAX_NAME} characters.` };
  if (/[<>"\\]/.test(name)) return { ok: false, problem: 'The sender name cannot hold < > " or \\.' };
  const address = draft.senderAddress.trim();
  if (address === "" && draft.enabled) return { ok: false, problem: "Enter the sender address before switching the email on." };
  if (address !== "" && !ADDRESS.test(address)) {
    return { ok: false, problem: "The sender address does not look like an address; use the form report@mail.example.com." };
  }
  const link = draft.linkUrl.trim();
  if (link !== "" && (link.length > MAX_LINK || !LINK.test(link))) {
    return { ok: false, problem: "The link must be a web address starting with http:// or https://." };
  }
  return {
    ok: true,
    schedule: {
      enabled: draft.enabled,
      time,
      days: [...draft.days].sort((a, b) => a - b),
      sender_name: name,
      sender_address: address,
      link_url: link,
    },
  };
}

/** Whether the form differs from what is saved (spaces round a typed value do not count). */
export function isScheduleChanged(draft: ScheduleDraft, saved: MailSchedule): boolean {
  const result = fromScheduleDraft(draft);
  return !result.ok || JSON.stringify(result.schedule) !== JSON.stringify({ ...saved, days: [...saved.days].sort((a, b) => a - b) });
}

/** The days with `day` added, or taken away if it is there: in order. */
export function toggleDay(days: readonly number[], day: number): number[] {
  return (days.includes(day) ? days.filter((d) => d !== day) : [...days, day]).sort((a, b) => a - b);
}

/** What became of the last try with an address, in words. */
export function recipientResult(recipient: MailRecipient, timeZone: string, nowMs: number): { level: Level; text: string } {
  if (recipient.last_status === null || recipient.last_at === null) {
    return { level: "neutral", text: "Nothing has been sent to this address yet." };
  }
  const what = recipient.last_kind === "test" ? "Test mail" : "Daily mail";
  const when = atText(recipient.last_at, timeZone, nowMs);
  const why = recipient.last_detail ? ` ${recipient.last_detail}` : "";
  if (recipient.last_status === "sent") return { level: "good", text: `${what} sent ${when}.` };
  if (recipient.last_status === "retrying") return { level: "warning", text: `${what} did not get through ${when}; it will be tried again.${why}` };
  return { level: "critical", text: `${what} did not get through ${when}.${why}` };
}

/** How one scheduled mail went, in a line: when it was for, and who got it. */
export function sendSummary(send: MailSend, timeZone: string, nowMs: number): { level: Level; text: string } {
  const when = momentText(send.scheduled_at, timeZone, nowMs);
  if (send.status === "skipped") return { level: "warning", text: `${when}: not sent. ${send.note ?? ""}`.trim() };
  const total = send.sent + send.failed + send.pending + send.cancelled;
  if (total === 0) return { level: "neutral", text: `${when}: there was nobody to send it to.` };
  if (send.pending > 0) return { level: "warning", text: `${when}: being sent, ${send.sent} of ${total} so far.` };
  if (send.failed > 0) return { level: "critical", text: `${when}: sent to ${send.sent} of ${total}, ${send.failed} failed.` };
  if (send.cancelled > 0) return { level: "neutral", text: `${when}: sent to ${send.sent} of ${total}, the rest were stopped (the email was switched off).` };
  return { level: "good", text: `${when}: sent to ${send.sent} of ${total}.` };
}

/** When the next one goes ("The next one goes out tomorrow at 00:01."), or an empty text if none is due. */
export function nextText(state: MailState, nowMs: number): string {
  return state.next_at === null ? "" : `The next one goes out ${whenText(state.next_at, nowMs, state.timezone)}.`;
}

/** Whether the email is on, off or stuck, in a word and in a sentence. */
export function statusLine(state: MailState, nowMs: number): { level: Level; short: string; text: string } {
  const missing = state.problems.join(" ");
  if (!state.schedule.enabled) {
    return { level: "neutral", short: "Off", text: missing ? `The daily email is off. Before it can send: ${missing}` : "The daily email is off." };
  }
  if (state.problems.length > 0) return { level: "warning", short: "Cannot send yet", text: `It is on, but it cannot send yet. ${missing}` };
  const next = nextText(state, nowMs);
  return { level: "good", short: "On", text: next ? `On. ${next}` : "On." };
}

/** The newest scheduled mail as a line, for the System page; none yet is said as that. */
export function lastMailLine(state: MailState, nowMs: number): { level: Level; text: string } {
  const newest = state.recent[0];
  return newest === undefined ? { level: "neutral", text: "No daily email has been due yet." } : sendSummary(newest, state.timezone, nowMs);
}
