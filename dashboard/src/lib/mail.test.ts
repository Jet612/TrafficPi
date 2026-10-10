import { describe, expect, it } from "vitest";
import {
  daysText,
  fromScheduleDraft,
  isScheduleChanged,
  lastMailLine,
  nextText,
  recipientResult,
  sendSummary,
  statusLine,
  toScheduleDraft,
  toggleDay,
} from "./mail";
import type { MailRecipient, MailSchedule, MailSend, MailState } from "./types";

const FRIDAY_8PM = Date.UTC(2026, 9, 10, 0, 0); // 20:00 on 9 October in New York
const ZONE = "America/New_York";

const saved: MailSchedule = {
  enabled: true,
  time: "20:00",
  days: [0, 1, 2, 3, 4, 5, 6],
  sender_name: "TrafficPi",
  sender_address: "report@mail.example.com",
  link_url: "",
};

function state(over: Partial<MailState> = {}): MailState {
  return {
    schedule: saved,
    recipients: [],
    key_set: true,
    transport: "resend",
    ready: true,
    problems: [],
    next_at: FRIDAY_8PM,
    timezone: ZONE,
    recent: [],
    max_recipients: 20,
    ...over,
  };
}

function recipient(over: Partial<MailRecipient> = {}): MailRecipient {
  return { id: 1, address: "a@example.com", added_at: 0, last_at: null, last_kind: null, last_status: null, last_detail: null, ...over };
}

function send(over: Partial<MailSend> = {}): MailSend {
  return {
    id: 1,
    scheduled_at: FRIDAY_8PM,
    period_start: FRIDAY_8PM - 86_400_000,
    period_end: FRIDAY_8PM,
    status: "done",
    note: null,
    subject: "s",
    sent: 3,
    failed: 0,
    pending: 0,
    cancelled: 0,
    ...over,
  };
}

describe("days", () => {
  it("are told as a phrase when they have one and by name otherwise", () => {
    expect(daysText([0, 1, 2, 3, 4, 5, 6])).toBe("every day");
    expect(daysText([4, 3, 2, 1, 0])).toBe("weekdays");
    expect(daysText([6, 5])).toBe("weekends");
    expect(daysText([0, 2, 4])).toBe("Mon, Wed, Fri");
    expect(daysText([3])).toBe("Thu");
    expect(daysText([])).toBe("");
  });

  it("are toggled in order", () => {
    expect(toggleDay([0, 2], 1)).toEqual([0, 1, 2]);
    expect(toggleDay([0, 1, 2], 1)).toEqual([0, 2]);
    expect(toggleDay([], 6)).toEqual([6]);
    expect(toggleDay([3], 3)).toEqual([]);
  });
});

describe("the schedule form", () => {
  it("shows what is saved and sends it back as it was", () => {
    const draft = toScheduleDraft(saved);
    expect(draft).toEqual({ enabled: true, time: "20:00", days: [0, 1, 2, 3, 4, 5, 6], senderName: "TrafficPi", senderAddress: "report@mail.example.com", linkUrl: "" });
    expect(fromScheduleDraft(draft)).toEqual({ ok: true, schedule: saved });
  });

  it("does not share the days with what is saved", () => {
    const draft = toScheduleDraft(saved);
    draft.days.pop();
    expect(saved.days).toHaveLength(7);
  });

  it("trims what is typed and keeps the days in order", () => {
    const result = fromScheduleDraft({ enabled: true, time: " 07:30 ", days: [4, 0, 2], senderName: "  Elm Street cam ", senderAddress: " report@mail.example.com ", linkUrl: " https://traffic.example.com " });
    expect(result).toEqual({
      ok: true,
      schedule: { enabled: true, time: "07:30", days: [0, 2, 4], sender_name: "Elm Street cam", sender_address: "report@mail.example.com", link_url: "https://traffic.example.com" },
    });
  });

  it.each(["", "8pm", "24:00", "20:60", "2000", "20:0", "ab:cd"])("refuses the time %j in words", (time) => {
    expect(fromScheduleDraft({ ...toScheduleDraft(saved), time })).toEqual({ ok: false, problem: "Enter the time as HH:MM, like 20:00." });
  });

  it("wants at least one day", () => {
    expect(fromScheduleDraft({ ...toScheduleDraft(saved), days: [] })).toEqual({ ok: false, problem: "Pick at least one day of the week." });
  });

  it("wants a sender address to switch it on but not to leave it off", () => {
    const blank = { ...toScheduleDraft(saved), senderAddress: " " };
    expect(fromScheduleDraft(blank)).toEqual({ ok: false, problem: "Enter the sender address before switching the email on." });
    expect(fromScheduleDraft({ ...blank, enabled: false })).toMatchObject({ ok: true, schedule: { enabled: false, sender_address: "" } });
  });

  it.each(["plain", "a@b", "Name <a@b.io>", "a@b.io, c@d.io", "a b@c.io"])("refuses the sender address %j", (senderAddress) => {
    const result = fromScheduleDraft({ ...toScheduleDraft(saved), senderAddress });
    expect(result.ok).toBe(false);
    expect(result.ok ? "" : result.problem).toContain("sender address");
  });

  it.each(["x".repeat(61), "Jet <jet@x.com>", 'say "hi"', "a\\b"])("refuses the sender name %j", (senderName) => {
    const result = fromScheduleDraft({ ...toScheduleDraft(saved), senderName });
    expect(result.ok).toBe(false);
    expect(result.ok ? "" : result.problem).toContain("sender name");
  });

  it.each(["javascript:alert(1)", "example.com", "ftp://example.com", "https://exa mple.com", 'https://x.com/"y', "https://" + "a".repeat(300) + ".com"])("refuses the link %j", (linkUrl) => {
    const result = fromScheduleDraft({ ...toScheduleDraft(saved), linkUrl });
    expect(result).toEqual({ ok: false, problem: "The link must be a web address starting with http:// or https://." });
  });

  it("accepts an empty link and a web address", () => {
    expect(fromScheduleDraft({ ...toScheduleDraft(saved), linkUrl: "" }).ok).toBe(true);
    expect(fromScheduleDraft({ ...toScheduleDraft(saved), linkUrl: "http://192.168.1.20:8000" }).ok).toBe(true);
  });

  it("knows when something has changed, and when it only looks it", () => {
    expect(isScheduleChanged(toScheduleDraft(saved), saved)).toBe(false);
    expect(isScheduleChanged({ ...toScheduleDraft(saved), time: " 20:00 " }, saved)).toBe(false);
    expect(isScheduleChanged({ ...toScheduleDraft(saved), senderName: " TrafficPi" }, saved)).toBe(false);
    expect(isScheduleChanged({ ...toScheduleDraft(saved), time: "20:01" }, saved)).toBe(true);
    expect(isScheduleChanged({ ...toScheduleDraft(saved), enabled: false }, saved)).toBe(true);
    expect(isScheduleChanged({ ...toScheduleDraft(saved), days: [0, 1] }, saved)).toBe(true);
    expect(isScheduleChanged({ ...toScheduleDraft(saved), linkUrl: "https://x.example.com" }, saved)).toBe(true);
    expect(isScheduleChanged({ ...toScheduleDraft(saved), time: "" }, saved)).toBe(true); // half typed counts, though it cannot be saved
  });
});

// 20:30 on the evening of the 9th in New York, half an hour after FRIDAY_8PM
const NOW = FRIDAY_8PM + 30 * 60_000;

describe("what became of a recipient's last mail", () => {
  it("is nothing before anything was sent", () => {
    expect(recipientResult(recipient(), ZONE, NOW)).toEqual({ level: "neutral", text: "Nothing has been sent to this address yet." });
  });

  it("says that a test or a daily mail was sent, and when, in the camera's zone", () => {
    expect(recipientResult(recipient({ last_at: FRIDAY_8PM, last_kind: "test", last_status: "sent" }), ZONE, NOW + 3 * 3_600_000)).toEqual({
      level: "good",
      text: "Test mail sent at 20:00.",
    });
    expect(recipientResult(recipient({ last_at: FRIDAY_8PM, last_kind: "daily", last_status: "sent" }), "UTC", NOW).text).toBe("Daily mail sent 30 min ago.");
    expect(recipientResult(recipient({ last_at: FRIDAY_8PM, last_kind: "daily", last_status: "sent" }), ZONE, NOW + 24 * 3_600_000).text).toBe("Daily mail sent yesterday at 20:00.");
  });

  it("gives the reason when it did not get through", () => {
    const failed = recipientResult(recipient({ last_at: FRIDAY_8PM, last_kind: "test", last_status: "failed", last_detail: "Resend refused it (403): domain is not verified" }), ZONE, NOW + 3 * 3_600_000);
    expect(failed.level).toBe("critical");
    expect(failed.text).toBe("Test mail did not get through at 20:00. Resend refused it (403): domain is not verified");
  });

  it("says that it will be tried again", () => {
    const retrying = recipientResult(recipient({ last_at: FRIDAY_8PM, last_kind: "daily", last_status: "retrying", last_detail: "Could not reach Resend" }), ZONE, NOW);
    expect(retrying.level).toBe("warning");
    expect(retrying.text).toContain("tried again");
    expect(retrying.text).toContain("Could not reach Resend");
  });

  it("does not make up a reason", () => {
    expect(recipientResult(recipient({ last_at: FRIDAY_8PM, last_kind: "daily", last_status: "failed" }), ZONE, NOW + 3 * 3_600_000).text).toBe("Daily mail did not get through at 20:00.");
  });
});

describe("how a scheduled mail went", () => {
  const later = FRIDAY_8PM + 3 * 3_600_000; // the same evening: the mail's time is told as a clock time
  it.each([
    [{}, "good", "20:00: sent to 3 of 3."],
    [{ sent: 2, failed: 1 }, "critical", "20:00: sent to 2 of 3, 1 failed."],
    [{ sent: 1, pending: 2 }, "warning", "20:00: being sent, 1 of 3 so far."],
    [{ sent: 1, cancelled: 2 }, "neutral", "20:00: sent to 1 of 3, the rest were stopped (the email was switched off)."],
    [{ sent: 0, failed: 0, pending: 0, cancelled: 0 }, "neutral", "20:00: there was nobody to send it to."],
  ] as const)("%j is %s", (over, level, text) => {
    expect(sendSummary(send(over), ZONE, later)).toEqual({ level, text });
  });

  it("names the day when it was another one", () => {
    expect(sendSummary(send(), ZONE, FRIDAY_8PM + 3 * 86_400_000).text).toBe("Fri, Oct 9, 20:00: sent to 3 of 3.");
  });

  it("says why a skipped one was skipped", () => {
    expect(sendSummary(send({ status: "skipped", note: "Not sent: the Pi was not running.", sent: 0 }), ZONE, later)).toEqual({
      level: "warning",
      text: "20:00: not sent. Not sent: the Pi was not running.",
    });
  });
});

describe("the status of the email", () => {
  it("is off, and says what is missing before it could send", () => {
    const off = statusLine(state({ schedule: { ...saved, enabled: false }, problems: ["No recipient has been added."], ready: false, next_at: null }), NOW);
    expect(off).toEqual({ level: "neutral", short: "Off", text: "The daily email is off. Before it can send: No recipient has been added." });
    expect(statusLine(state({ schedule: { ...saved, enabled: false }, next_at: null }), NOW).text).toBe("The daily email is off.");
  });

  it("is on but stuck, and says why", () => {
    const stuck = statusLine(state({ problems: ["No API key is set."], ready: false }), NOW);
    expect(stuck).toEqual({ level: "warning", short: "Cannot send yet", text: "It is on, but it cannot send yet. No API key is set." });
  });

  it("is on, and says when the next one goes", () => {
    expect(statusLine(state({ next_at: FRIDAY_8PM + 4 * 3_600_000 }), NOW)).toEqual({
      level: "good",
      short: "On",
      text: "On. The next one goes out tomorrow at 00:00.",
    });
    expect(nextText(state({ next_at: FRIDAY_8PM + 2 * 3_600_000 }), NOW)).toBe("The next one goes out today at 22:00.");
    expect(nextText(state({ next_at: FRIDAY_8PM + 3 * 86_400_000 }), NOW)).toBe("The next one goes out Mon, Oct 12 at 20:00.");
    expect(nextText(state({ next_at: null }), NOW)).toBe("");
  });

  it("tells the System page the newest mail, or that none has been due", () => {
    expect(lastMailLine(state(), NOW).text).toBe("No daily email has been due yet.");
    expect(lastMailLine(state({ recent: [send({ failed: 1, sent: 2 }), send({ id: 0 })] }), NOW).level).toBe("critical");
  });
});
