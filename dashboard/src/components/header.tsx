"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { LogOut } from "lucide-react";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/ui/status";
import { useLiveSelector } from "@/components/live-provider";
import { useSession } from "@/components/session";
import { useUnsaved } from "@/components/unsaved";
import { cn } from "@/lib/cn";
import { deriveStatus, type HeaderStatus } from "@/lib/connection-status";
import { PAGES, isCurrentPage } from "@/lib/pages";

const sameStatus = (a: HeaderStatus, b: HeaderStatus) =>
  a.level === b.level && a.label === b.label && a.extra === b.extra && a.detail === b.detail;
const selectStatus = (snapshot: Parameters<typeof deriveStatus>[0]) => deriveStatus(snapshot);

export function Header() {
  const { username, signOut } = useSession();
  const { confirmLeave } = useUnsaved();
  const pathname = usePathname();
  const status = useLiveSelector(selectStatus, sameStatus);
  return (
    <header className="border-b border-edge bg-surface">
      <div className="mx-auto flex max-w-[1280px] flex-wrap items-center justify-between gap-x-4 gap-y-2 px-4 py-3 sm:px-6">
        <div className="flex items-center gap-3">
          <Logo className="size-8 text-accent" />
          <p className="text-lg font-semibold">TrafficPi</p>
        </div>
        {/* On a phone the pages get a row of their own under the name and the state. */}
        <nav aria-label="Pages" className="order-last w-full sm:order-none sm:w-auto">
          <ul className="-mx-1 flex gap-1">
            {PAGES.map((page) => {
              const current = isCurrentPage(pathname, page.href);
              return (
                <li key={page.href}>
                  <Link
                    href={page.href}
                    aria-current={current ? "page" : undefined}
                    onClick={(event) => {
                      if (!current && !confirmLeave()) event.preventDefault();
                    }}
                    className={cn(
                      "inline-flex h-9 items-center rounded-lg px-3 text-sm font-medium transition-colors",
                      current ? "bg-sunken text-ink" : "text-ink-2 hover:bg-sunken hover:text-ink",
                    )}
                  >
                    {page.label}
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>
        <div className="flex items-center gap-3">
          <StatusBadge level={status.level}>
            {/* Only the state is announced to a screen reader; the changing frame rate is not. */}
            <span role="status" title={status.detail}>
              {status.label}
            </span>
            {status.extra && <span className="text-ink-2">&middot; {status.extra}</span>}
          </StatusBadge>
          <span className="hidden text-sm text-ink-2 sm:inline">{username}</span>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              if (confirmLeave()) void signOut();
            }}
          >
            <LogOut className="size-4" aria-hidden="true" />
            Sign out
          </Button>
        </div>
      </div>
    </header>
  );
}
