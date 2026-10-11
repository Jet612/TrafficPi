"use client";

import { useCallback, useEffect, useId, useRef, useState } from "react";
import { DayButtons } from "@/components/day-buttons";
import { MailKey } from "@/components/mail-key";
import { MailPreview } from "@/components/mail-preview";
import { MailRecipients } from "@/components/mail-recipients";
import { Button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Disclosure } from "@/components/ui/disclosure";
import { HowItWorks } from "@/components/ui/how-it-works";
import { Input, Label } from "@/components/ui/input";
import { OptionGroup } from "@/components/ui/option-group";
import { StatusBadge } from "@/components/ui/status";
import { Switch } from "@/components/ui/switch";
import { TimeField } from "@/components/ui/time-field";
import { useUnsavedWarning } from "@/components/unsaved";
import { ApiError, api } from "@/lib/api";
import {
  MAX_LINK,
  MAX_NAME,
  daysText,
  fromScheduleDraft,
  nextText,
  isScheduleChanged,
  sendSummary,
  statusLine,
  toScheduleDraft,
  type ScheduleDraft,
} from "@/lib/mail";
import { useNow } from "@/lib/hooks";
import type { MailState } from "@/lib/types";

const REFRESH_MS = 20_000;

type Outcome = { kind: "ok" | "error"; text: string } | null;

/** The daily email: when it goes, to whom, the provider's key, a preview and how the latest ones went. */
export function MailCard() {
  const [state, setState] = useState<MailState | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  // An answer to something the owner did is newer than a refresh that was already on its way.
  const epoch = useRef(0);
  const apply = useCallback((next: MailState) => {
    epoch.current += 1;
    setState(next);
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    const load = () => {
      const started = epoch.current;
      api.mail(controller.signal).then(
        (next) => {
          if (epoch.current === started) setState(next);
        },
        (error: unknown) => {
          if (controller.signal.aborted) return;
          console.error("could not load the daily email's state", error);
          setLoadFailed(true);
        },
      );
    };
    load();
    const timer = setInterval(load, REFRESH_MS);
    return () => {
      controller.abort();
      clearInterval(timer);
    };
  }, []);

  const now = useNow(60_000);
  const status = state ? statusLine(state, now) : null;
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Daily email</CardTitle>
          <CardSubtitle>A short report of the numbers, by email, at the time you choose.</CardSubtitle>
        </div>
        {status && <StatusBadge level={status.level}>{status.short}</StatusBadge>}
      </CardHeader>
      <CardBody>
        {state === null ? (
          <p className="text-sm text-ink-2">{loadFailed ? "The daily email's settings could not be loaded." : "Loading…"}</p>
        ) : (
          <div className="grid gap-6">
            <HowItWorks>
              <p>
                At the time you choose, TrafficPi emails a short report of the time since the last one: how many vehicles did not stop, rolled through and stopped at your stop signs,
                the stop rate, the busiest hour and the vehicles counted. It holds numbers only: no pictures and no clips leave this machine, and the addresses and the numbers are the
                only things sent, to the mail provider (Resend), which delivers them.
              </p>
              {state.transport === "file" && (
                <p>
                  <strong className="font-medium text-ink">Demo mode:</strong> this copy writes each mail to a folder (<code>outbox</code>, in the data directory) instead of sending
                  it, and needs no key. An address that starts with <code>fail@</code> is refused, to show what a failure looks like.
                </p>
              )}
            </HowItWorks>
            <div className="grid gap-x-10 gap-y-6 md:grid-cols-2">
              <ScheduleForm state={state} apply={apply} />
              <div className="grid content-start gap-6">
                <MailRecipients state={state} apply={apply} />
                {state.transport === "resend" && (
                  <Disclosure title="Resend API key" status={<StatusBadge level={state.key_set ? "good" : "warning"}>{state.key_set ? "Saved" : "No key yet"}</StatusBadge>} defaultOpen={!state.key_set}>
                    <MailKey state={state} apply={apply} />
                  </Disclosure>
                )}
              </div>
            </div>
            <MailPreview />
            <RecentMails state={state} now={now} />
          </div>
        )}
      </CardBody>
    </Card>
  );
}

function ScheduleForm({ state, apply }: { state: MailState; apply: (next: MailState) => void }) {
  const [edits, setEdits] = useState<Partial<ScheduleDraft>>({});
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<Outcome>(null);
  const now = useNow(60_000);
  const next = nextText(state, now);
  const timeId = useId();
  const timeHelpId = useId();
  const nameId = useId();
  const addressId = useId();
  const addressHelpId = useId();
  const linkId = useId();
  const linkHelpId = useId();

  // What is saved, with what has been typed over it: a refresh of the saved state never undoes an edit.
  const draft: ScheduleDraft = { ...toScheduleDraft(state.schedule), ...edits };
  const checked = fromScheduleDraft(draft);
  const problem = checked.ok ? null : checked.problem;
  const dirty = isScheduleChanged(draft, state.schedule);
  useUnsavedWarning(dirty);

  function change(update: Partial<ScheduleDraft>) {
    setOutcome(null);
    setEdits((current) => ({ ...current, ...update }));
  }

  async function save() {
    if (!checked.ok) return;
    setBusy(true);
    setOutcome(null);
    try {
      apply(await api.saveMailSchedule(checked.schedule));
      setEdits({});
      setOutcome({ kind: "ok", text: "Saved." });
    } catch (error) {
      setOutcome({ kind: "error", text: error instanceof ApiError ? error.message : "Could not save." });
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      aria-label="Daily email schedule"
      className="grid content-start gap-4"
      noValidate // the form says what is wrong in words; the browser's own bubble would hide that
      onSubmit={(event) => {
        event.preventDefault();
        void save();
      }}
    >
      <h3 className="text-sm font-medium text-ink">When it goes</h3>
      <Switch label="Send the daily email" checked={draft.enabled} onChange={(enabled) => change({ enabled })} />
      <div>
        <Label
          htmlFor={timeId}
          hintId={timeHelpId}
          hint={<>By the clock of the camera&rsquo;s time zone, {state.timezone}. Each mail covers the time since the one before it.</>}
        >
          Send it at
        </Label>
        <TimeField id={timeId} value={draft.time} onChange={(time) => change({ time })} aria-describedby={timeHelpId} />
        {draft.enabled && next !== "" && <p className="mt-1.5 text-sm text-ink-2">{next}</p>}
      </div>
      <OptionGroup
        title="On these days"
        className="gap-1.5"
        hint={
          draft.days.length === 0
            ? "The days of the week that the email goes out on."
            : `Goes out ${daysText(draft.days)}.${!draft.days.includes(5) && !draft.days.includes(6) ? " The first mail after a weekend covers the weekend too." : ""}`
        }
      >
        <DayButtons days={draft.days} onChange={(days) => change({ days })} />
        {draft.days.length === 0 && <p className="text-sm text-critical">Pick at least one day.</p>}
      </OptionGroup>
      <Disclosure title="Sender and link" status={<span className="text-sm font-normal text-ink-2">{draft.senderAddress || "not set"}</span>} defaultOpen={state.schedule.sender_address === ""}>
      <div>
        <Label htmlFor={nameId}>Sender name</Label>
        <Input
          id={nameId}
          value={draft.senderName}
          onChange={(event) => change({ senderName: event.target.value })}
          maxLength={MAX_NAME + 20}
          autoComplete="off"
          className="max-w-sm"
        />
      </div>
      <div>
        <Label htmlFor={addressId} hintId={addressHelpId} hint="An address on the domain you verified with Resend. Mail from any other address is refused.">
          Sender address
        </Label>
        <Input
          id={addressId}
          type="email"
          value={draft.senderAddress}
          onChange={(event) => change({ senderAddress: event.target.value })}
          placeholder="report@mail.example.com"
          autoComplete="off"
          spellCheck={false}
          aria-describedby={addressHelpId}
          className="max-w-sm"
        />
      </div>
      <div>
        <Label
          htmlFor={linkId}
          hintId={linkHelpId}
          hint={
            <>
              If you fill this in, the mail has an &ldquo;Open the dashboard&rdquo; link to it. This Pi&rsquo;s own address only works at home; a tunnel&rsquo;s address works
              anywhere.
            </>
          }
        >
          Link to this dashboard (optional)
        </Label>
        <Input
          id={linkId}
          value={draft.linkUrl}
          onChange={(event) => change({ linkUrl: event.target.value })}
          maxLength={MAX_LINK + 20}
          placeholder="https://traffic.example.com"
          autoComplete="off"
          spellCheck={false}
          aria-describedby={linkHelpId}
          className="max-w-sm"
        />
      </div>
      </Disclosure>
      <div className="flex flex-wrap items-center gap-3">
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
            <span className={`mr-1.5 inline-block size-2 rounded-full align-middle ${outcome.kind === "error" ? "bg-critical" : "bg-good"}`} aria-hidden="true" />
            <span className="font-medium">{outcome.kind === "error" ? "Not saved. " : ""}</span>
            {outcome.text}
          </p>
        )}
      </div>
    </form>
  );
}

function RecentMails({ state, now }: { state: MailState; now: number }) {
  if (state.recent.length === 0) return null;
  return (
    <section aria-label="Recent daily emails">
      <h3 className="mb-2 text-sm font-medium text-ink">The latest ones</h3>
      <ul className="grid gap-1.5">
        {state.recent.map((send) => {
          const line = sendSummary(send, state.timezone, now);
          return (
            <li key={send.id}>
              <StatusBadge level={line.level}>{line.text}</StatusBadge>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
