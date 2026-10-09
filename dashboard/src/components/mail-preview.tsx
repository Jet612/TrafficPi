"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { ApiError, api } from "@/lib/api";
import type { MailPreview as Preview } from "@/lib/types";

type View = "mail" | "text";

/**
 * The mail as it would be made now, for the past 24 hours. Nothing is sent. The HTML is shown in an
 * iframe with every sandbox restriction on (no scripts, no forms, no links followed, not the page's
 * own origin), so that nothing in it can touch the dashboard.
 */
export function MailPreview() {
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [view, setView] = useState<View>("mail");

  async function load() {
    setBusy(true);
    setError(null);
    try {
      setPreview(await api.mailPreview());
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Could not make the preview.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Preview of the daily email" className="grid gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-medium text-ink">What it looks like</h3>
        <Button type="button" size="sm" busy={busy} onClick={() => void load()}>
          {preview ? "Refresh the preview" : "Preview the mail"}
        </Button>
        {preview && (
          <>
            <Segmented
              className="w-fit"
              label="Preview as"
              value={view}
              options={[
                { value: "mail", label: "Mail" },
                { value: "text", label: "Plain text" },
              ]}
              onChange={setView}
            />
            <Button type="button" size="sm" variant="ghost" onClick={() => setPreview(null)}>
              Close the preview
            </Button>
          </>
        )}
      </div>
      {error && (
        <p role="alert" className="text-sm text-critical">
          {error}
        </p>
      )}
      {preview && (
        <div className="grid gap-2">
          <p className="text-sm text-ink-2">
            Subject: <span className="font-medium text-ink">{preview.subject}</span>
          </p>
          {view === "mail" ? (
            <iframe title="Preview of the daily email" sandbox="" srcDoc={preview.html} className="h-[34rem] w-full max-w-xl rounded-lg border border-edge bg-white" />
          ) : (
            <pre className="max-w-xl overflow-x-auto whitespace-pre-wrap rounded-lg border border-edge bg-sunken p-3 text-sm text-ink">{preview.text}</pre>
          )}
          <p className="text-sm text-ink-2">The numbers are for the past 24 hours up to now; the real mail covers the time between two scheduled times.</p>
        </div>
      )}
    </section>
  );
}
