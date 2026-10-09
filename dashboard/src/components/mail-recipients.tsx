"use client";

import { useId, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { StatusBadge } from "@/components/ui/status";
import { ApiError, api } from "@/lib/api";
import { recipientResult } from "@/lib/mail";
import type { MailState } from "@/lib/types";

type Note = { id: number; ok: boolean; text: string };

/** The addresses that get the email: add, remove, and send each a test that says how it went. */
export function MailRecipients({ state, apply }: { state: MailState; apply: (next: MailState) => void }) {
  const [address, setAddress] = useState("");
  const [adding, setAdding] = useState(false);
  const [addError, setAddError] = useState<string | null>(null);
  const [testing, setTesting] = useState<number | null>(null);
  const [note, setNote] = useState<Note | null>(null); // how the latest test went
  const [rowError, setRowError] = useState<string | null>(null);
  const addressId = useId();
  const full = state.recipients.length >= state.max_recipients;

  async function add() {
    if (address.trim() === "") return;
    setAdding(true);
    setAddError(null);
    try {
      apply(await api.addMailRecipient(address));
      setAddress("");
    } catch (error) {
      setAddError(error instanceof ApiError ? error.message : "Could not add it.");
    } finally {
      setAdding(false);
    }
  }

  async function test(id: number) {
    setTesting(id);
    setRowError(null);
    setNote(null);
    try {
      const result = await api.testMail(id);
      setNote(
        result.ok
          ? { id, ok: true, text: "Sent. Look in that inbox (and the spam folder)." }
          : { id, ok: false, text: result.detail || "It did not get through." },
      );
      apply(await api.mail()); // the result is kept with the address; show it
    } catch (error) {
      setRowError(error instanceof ApiError ? error.message : "Could not send the test.");
    } finally {
      setTesting(null);
    }
  }

  async function remove(id: number) {
    setRowError(null);
    try {
      apply(await api.removeMailRecipient(id));
    } catch (error) {
      setRowError(error instanceof ApiError ? error.message : "Could not remove it.");
    }
  }

  return (
    <section aria-label="Recipients" className="grid content-start gap-3">
      <h3 className="text-sm font-medium text-ink">Who gets it</h3>
      {state.recipients.length === 0 ? (
        <p className="text-sm text-ink-2">Nobody yet. Add the addresses that should get the report.</p>
      ) : (
        <ul className="grid gap-2">
          {state.recipients.map((recipient) => {
            const result = recipientResult(recipient, state.timezone);
            const shown = note?.id === recipient.id ? note : null;
            return (
              <li key={recipient.id} className="rounded-lg border border-edge p-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="break-all font-medium text-ink">{recipient.address}</span>
                  <span className="flex gap-2">
                    <Button
                      type="button"
                      size="sm"
                      busy={testing === recipient.id}
                      disabled={testing !== null}
                      aria-label={`Send a test to ${recipient.address}`}
                      onClick={() => void test(recipient.id)}
                    >
                      Send a test
                    </Button>
                    <Button type="button" size="sm" variant="ghost" aria-label={`Remove ${recipient.address}`} onClick={() => void remove(recipient.id)}>
                      Remove
                    </Button>
                  </span>
                </div>
                <p className="mt-2 text-sm text-ink-2">
                  <StatusBadge level={result.level} className="mr-2 max-w-full whitespace-normal">
                    {result.text}
                  </StatusBadge>
                </p>
                {shown && (
                  <p role={shown.ok ? "status" : "alert"} className="mt-2 text-sm text-ink">
                    {shown.ok ? "" : "The test did not get through: "}
                    {shown.text}
                  </p>
                )}
              </li>
            );
          })}
        </ul>
      )}
      {rowError && (
        <p role="alert" className="text-sm text-critical">
          {rowError}
        </p>
      )}
      <form
        aria-label="Add a recipient"
        noValidate // the server says what is wrong with an address, in words, as it does everywhere
        onSubmit={(event) => {
          event.preventDefault();
          void add();
        }}
      >
        <Label htmlFor={addressId}>Add an address</Label>
        <div className="flex flex-wrap gap-2">
          <Input
            id={addressId}
            type="email"
            value={address}
            onChange={(event) => {
              setAddress(event.target.value);
              setAddError(null);
            }}
            placeholder="name@example.com"
            autoComplete="off"
            spellCheck={false}
            disabled={full}
            className="max-w-xs"
          />
          <Button type="submit" busy={adding} disabled={full || adding || address.trim() === ""}>
            Add
          </Button>
        </div>
        <p className="mt-1.5 text-sm text-ink-2">
          {state.recipients.length} of {state.max_recipients}. {full ? "The list is full: remove an address to add another. " : ""}Each address gets its own copy, so nobody sees the others.
        </p>
        {addError && (
          <p role="alert" className="mt-1.5 text-sm text-critical">
            {addError}
          </p>
        )}
      </form>
    </section>
  );
}
