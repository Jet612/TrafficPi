"use client";

import { Header } from "@/components/header";
import { LiveProvider } from "@/components/live-provider";
import { LoginScreen } from "@/components/login-screen";
import { Logo } from "@/components/logo";
import { SessionProvider, useSession } from "@/components/session";
import { StatsProvider } from "@/components/stats-provider";
import { Button } from "@/components/ui/button";
import { UnsavedProvider } from "@/components/unsaved";

/**
 * Everything the pages share: the session, the live socket, today's numbers and the header with
 * its navigation. It is the layout of every page, so moving between pages keeps all of it (the
 * socket does not reconnect, nothing is fetched twice).
 */
export function App({ children }: { children: React.ReactNode }) {
  return (
    <SessionProvider>
      <Gate>{children}</Gate>
    </SessionProvider>
  );
}

function Gate({ children }: { children: React.ReactNode }) {
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
      <UnsavedProvider>
        <StatsProvider>
          <a
            href="#main"
            className="sr-only rounded-lg bg-accent px-4 py-2 text-accent-ink focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50"
          >
            Skip to the dashboard
          </a>
          <Header />
          <main id="main" className="mx-auto max-w-[1280px] px-4 py-4 sm:px-6 sm:py-6">
            {children}
          </main>
        </StatsProvider>
      </UnsavedProvider>
    </LiveProvider>
  );
}
