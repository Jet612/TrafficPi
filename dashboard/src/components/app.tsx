"use client";

import { usePathname } from "next/navigation";
import { DriftBanner } from "@/components/drift-banner";
import { Header } from "@/components/header";
import { LiveProvider } from "@/components/live-provider";
import { LoginScreen } from "@/components/login-screen";
import { Logo } from "@/components/logo";
import { SessionProvider, useSession } from "@/components/session";
import { StatsProvider } from "@/components/stats-provider";
import { Button } from "@/components/ui/button";
import { UnsavedProvider } from "@/components/unsaved";
import { cn } from "@/lib/cn";
import { isCurrentPage } from "@/lib/pages";

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
          <Shell>{children}</Shell>
        </StatsProvider>
      </UnsavedProvider>
    </LiveProvider>
  );
}

/**
 * The page's frame: the header, the camera-drift notice and the room the page has. The live page
 * is given a wider room so that the picture grows with a big screen; the others read best at a line
 * length of 1280 px. The header and the page share the width (`--shell`), so that they line up.
 */
function Shell({ children }: { children: React.ReactNode }) {
  const wide = isCurrentPage(usePathname(), "/");
  return (
    <div className={cn("min-h-dvh", wide ? "[--shell:100rem]" : "[--shell:80rem]")}>
      <a
        href="#main"
        className="sr-only rounded-lg bg-accent px-4 py-2 text-accent-ink focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50"
      >
        Skip to the dashboard
      </a>
      <Header />
      <DriftBanner />
      {/* On a phone the bottom padding keeps the last card clear of the bar of pages fixed under it. */}
      <main id="main" className="mx-auto max-w-(--shell) px-4 pb-24 pt-4 sm:px-6 sm:pb-6 sm:pt-6">
        {children}
      </main>
    </div>
  );
}
