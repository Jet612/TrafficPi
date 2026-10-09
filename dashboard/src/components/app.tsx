"use client";

import { Dashboard } from "@/components/dashboard";
import { LiveProvider } from "@/components/live-provider";
import { LoginScreen } from "@/components/login-screen";
import { Logo } from "@/components/logo";
import { SessionProvider, useSession } from "@/components/session";
import { Button } from "@/components/ui/button";

export function App() {
  return (
    <SessionProvider>
      <Gate />
    </SessionProvider>
  );
}

function Gate() {
  const { status, recheck } = useSession();
  if (status === "loading") {
    return (
      <main className="grid min-h-dvh place-items-center">
        <div role="status" className="flex items-center gap-3 text-ink-2">
          <Logo className="size-8 text-accent" />
          Loading&hellip;
        </div>
      </main>
    );
  }
  if (status === "unreachable") {
    return (
      <main className="mx-auto flex min-h-dvh max-w-sm flex-col justify-center gap-4 px-4 text-center">
        <Logo className="mx-auto size-10 text-accent" />
        <h1 className="text-lg font-semibold">Cannot reach TrafficPi</h1>
        <p className="text-ink-2">Check that the Pi is switched on and that this device is on the same network.</p>
        <Button variant="primary" onClick={recheck} className="mx-auto">Try again</Button>
      </main>
    );
  }
  if (status === "anonymous") return <LoginScreen />;
  return (
    <LiveProvider onRefused={recheck}>
      <Dashboard />
    </LiveProvider>
  );
}
