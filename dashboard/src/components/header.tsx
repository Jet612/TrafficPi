"use client";

import { LogOut } from "lucide-react";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status";
import { useLiveSelector } from "@/components/live-provider";
import { useSession } from "@/components/session";
import { deriveStatus, type HeaderStatus } from "@/lib/connection-status";

const sameStatus = (a: HeaderStatus, b: HeaderStatus) =>
  a.level === b.level && a.label === b.label && a.extra === b.extra && a.detail === b.detail;
const selectStatus = (snapshot: Parameters<typeof deriveStatus>[0]) => deriveStatus(snapshot);

export function Header() {
  const { username, signOut } = useSession();
  const status = useLiveSelector(selectStatus, sameStatus);
  return (
    <header className="border-b border-edge bg-surface">
      <div className="mx-auto flex max-w-[1280px] flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <Logo className="size-8 text-accent" />
          <h1 className="text-lg font-semibold">TrafficPi</h1>
        </div>
        <div className="flex items-center gap-3">
          <StatusBadge level={status.level}>
            {/* Only the state is announced to a screen reader; the changing frame rate is not. */}
            <span role="status" title={status.detail}>
              {status.label}
            </span>
            {status.extra && <span className="text-ink-2">&middot; {status.extra}</span>}
          </StatusBadge>
          <span className="hidden text-sm text-ink-2 sm:inline">{username}</span>
          <Button variant="ghost" size="sm" onClick={() => void signOut()}>
            <LogOut className="size-4" aria-hidden="true" />
            Sign out
          </Button>
        </div>
      </div>
    </header>
  );
}
