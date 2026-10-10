"use client";

import Link from "next/link";
import { Mail } from "lucide-react";
import { Card, CardBody } from "@/components/ui/card";
import { StatusBadge } from "@/components/ui/status";
import { api } from "@/lib/api";
import { useNow, useRequest } from "@/lib/hooks";
import { lastMailLine, statusLine } from "@/lib/mail";

const REFRESH_MS = 60_000;

/**
 * The daily email on the System page: one line that says whether it is on, with a link to its settings,
 * and a sentence only when something is wrong (the latest mail failed, an address did not get it, it
 * is on but cannot send). Everything else about it is on the Setup page, once.
 */
export function MailSummary() {
  const { data: state, error } = useRequest("mail", (signal) => api.mail(signal), { refreshMs: REFRESH_MS });
  const now = useNow(60_000);
  if (state === null) {
    return error ? (
      <Card>
        <CardBody>
          <p className="text-sm text-ink-2">The daily email&rsquo;s state could not be loaded.</p>
        </CardBody>
      </Card>
    ) : null;
  }
  const status = statusLine(state, now);
  const last = lastMailLine(state, now);
  const failedAddress = state.recipients.some((recipient) => recipient.last_status === "failed");
  const wrong = last.level === "critical" || last.level === "warning";
  return (
    <Card>
      <CardBody className="grid gap-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          <h2 className="inline-flex items-center gap-2 text-base font-semibold text-ink">
            <Mail className="size-4 text-ink-3" aria-hidden="true" />
            Daily email
          </h2>
          <StatusBadge level={status.level}>{status.short}</StatusBadge>
          <Link href="/setup/" className="ml-auto text-sm font-medium text-accent underline underline-offset-2">
            Daily email settings
          </Link>
        </div>
        {status.level === "warning" && <p className="text-sm text-ink-2">{status.text}</p>}
        {wrong && (
          <p data-testid="last-mail" className="text-sm text-ink">
            <span className="text-ink-2">Latest: </span>
            {last.text}
          </p>
        )}
        {failedAddress && (
          <p role="status" className="text-sm text-critical">
            Some addresses did not get their last mail; the reasons are next to them on the Setup page.
          </p>
        )}
      </CardBody>
    </Card>
  );
}
