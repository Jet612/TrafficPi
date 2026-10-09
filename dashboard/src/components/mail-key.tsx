"use client";

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { StatusBadge } from "@/components/ui/status";
import { ApiError, api } from "@/lib/api";
import type { MailState } from "@/lib/types";

/**
 * The mail provider's API key. It is write-only: typed once, sent to the Pi, kept in a file that
 * only the TrafficPi user can read, and never shown again, not even here.
 */
export function MailKey({ state, apply }: { state: MailState; apply: (next: MailState) => void }) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);
  const keyId = useId();
  const helpId = useId();

  async function save() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      apply(await api.setMailKey(key));
      setKey(""); // it is not kept on this page either
      setSaved(true);
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Could not save the key.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    setBusy(true);
    setError(null);
    setSaved(false);
    try {
      apply(await api.removeMailKey());
    } catch (failure) {
      setError(failure instanceof ApiError ? failure.message : "Could not remove the key.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-label="Resend API key" className="grid content-start gap-3">
      <div className="flex flex-wrap items-center gap-3">
        <h3 className="text-sm font-medium text-ink">Resend API key</h3>
        {state.key_set ? <StatusBadge level="good">A key is saved</StatusBadge> : <StatusBadge level="warning">No key yet</StatusBadge>}
      </div>
      <form
        aria-label="Set the Resend API key"
        onSubmit={(event) => {
          event.preventDefault();
          void save();
        }}
      >
        <Label htmlFor={keyId}>{state.key_set ? "Replace the key" : "Paste the key"}</Label>
        <div className="flex flex-wrap gap-2">
          <Input
            id={keyId}
            type="password"
            name="resend-api-key"
            value={key}
            onChange={(event) => {
              setKey(event.target.value);
              setError(null);
              setSaved(false);
            }}
            autoComplete="off"
            spellCheck={false}
            aria-describedby={helpId}
            className="max-w-xs"
          />
          <Button type="submit" busy={busy} disabled={busy || key.trim() === ""}>
            Save the key
          </Button>
          {state.key_set && (
            <Button type="button" variant="ghost" disabled={busy} onClick={() => void remove()}>
              Remove the key
            </Button>
          )}
        </div>
        <p id={helpId} className="mt-1.5 text-sm text-ink-2">
          Create it in Resend with &ldquo;Sending access&rdquo; for your domain. It is kept on this Pi in a file that only the TrafficPi user can read, it is never shown again, and it
          goes nowhere but to Resend.
        </p>
        {error && (
          <p role="alert" className="mt-1.5 text-sm text-critical">
            {error}
          </p>
        )}
        {saved && (
          <p role="status" className="mt-1.5 text-sm text-ink">
            The key is saved. Send a test to an address to see that it works.
          </p>
        )}
      </form>
    </section>
  );
}
