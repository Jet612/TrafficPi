"use client";

import { useState } from "react";
import { ApiError } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Input, Label } from "@/components/ui/input";
import { Logo } from "@/components/logo";
import { useSession } from "@/components/session";

/** The sentence under a failed sign-in. It never says which of the two was wrong. */
export function loginErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "That username or password is not right.";
    if (error.status === 429) {
      const minutes = Math.max(1, Math.ceil((error.retryAfterS ?? 60) / 60));
      return `Too many failed attempts. Try again in ${minutes} minute${minutes === 1 ? "" : "s"}.`;
    }
    if (error.status === 0) return "Cannot reach the server. Check that the Pi is on and try again.";
    return error.message;
  }
  return "Something went wrong. Try again.";
}

export function LoginScreen() {
  const { signIn, configured } = useSession();
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(username, password);
    } catch (caught) {
      setError(loginErrorMessage(caught));
      setBusy(false);
    }
  }

  return (
    <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center px-4 py-10">
      <div className="mb-6 flex items-center gap-3">
        <Logo className="size-9 text-accent" />
        <h1 className="text-xl font-semibold">TrafficPi</h1>
      </div>
      <form onSubmit={submit} className="rounded-xl border border-edge bg-surface p-5" aria-label="Sign in">
        <h2 className="mb-4 text-base font-semibold">Sign in</h2>
        {configured === false && (
          <p className="mb-4 rounded-lg border border-edge bg-sunken p-3 text-sm text-ink-2" role="note">
            No password has been set yet. On the Pi, run <code className="rounded bg-surface px-1 py-0.5 font-mono text-ink">uv run trafficpi-admin set-password</code>{" "}
            from the <code className="font-mono text-ink">backend</code> folder, then sign in here.
          </p>
        )}
        <div className="mb-4">
          <Label htmlFor="username">Username</Label>
          <Input
            id="username"
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            required
          />
        </div>
        <div className="mb-5">
          <Label htmlFor="password">Password</Label>
          <Input
            id="password"
            name="password"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            autoFocus
            required
          />
        </div>
        <div aria-live="polite" className="min-h-6">
          {error && (
            <p role="alert" className="mb-3 text-sm font-medium text-ink">
              <span className="mr-1.5 inline-block size-2 rounded-full bg-critical align-middle" aria-hidden="true" />
              {error}
            </p>
          )}
        </div>
        <Button type="submit" variant="primary" className="w-full" busy={busy}>
          {busy ? "Signing in…" : "Sign in"}
        </Button>
      </form>
    </main>
  );
}
