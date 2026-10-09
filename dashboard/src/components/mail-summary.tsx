"use client";

import Link from "next/link";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { useRequest } from "@/lib/hooks";
import { lastMailLine, statusLine } from "@/lib/mail";

const REFRESH_MS = 60_000;

/** The daily email at a glance, for the System page: whether it is on, when the next goes, and how the last one went. */
export function MailSummary() {
  const { data: state, error } = useRequest("mail", (signal) => api.mail(signal), { refreshMs: REFRESH_MS });
  const status = state ? statusLine(state) : null;
  const last = state ? lastMailLine(state) : null;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Daily email</CardTitle>
        {status && <StatusBadge level={status.level}>{status.short}</StatusBadge>}
      </CardHeader>
      <CardBody>
        {state === null ? (
          <p className="text-sm text-ink-2">{error ? "The daily email's state could not be loaded." : "Loading…"}</p>
        ) : (
          <div className="grid gap-2 text-sm">
            <p className="text-ink">{status?.text}</p>
            {last && (
              <p data-testid="last-mail">
                <span className="text-ink-2">Latest: </span>
                {last.text}
              </p>
            )}
            {state.recipients.some((r) => r.last_status === "failed") && (
              <p role="status" className="text-critical">
                Some addresses did not get their last mail; the reasons are next to them on the Setup page.
              </p>
            )}
            <p className="text-ink-2">
              Who gets it, when, and the provider&rsquo;s key are set in the{" "}
              <Link href="/setup/" className="font-medium text-accent underline underline-offset-2">
                Daily email settings
              </Link>{" "}
              on the Setup page.
            </p>
          </div>
        )}
      </CardBody>
    </Card>
  );
}
