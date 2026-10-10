"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useId, useRef, useState } from "react";
import { ChartColumn, CircleDashed, CircleX, Cpu, History, LogOut, SlidersHorizontal, TriangleAlert, UserRound, Video, type LucideIcon } from "lucide-react";
import { Logo } from "@/components/logo";
import { Button } from "@/components/ui/button";
import { useLiveSelector } from "@/components/live-provider";
import { useSession } from "@/components/session";
import { useStats } from "@/components/stats-provider";
import { useUnsaved } from "@/components/unsaved";
import { cn } from "@/lib/cn";
import { deriveStatus, type HeaderStatus } from "@/lib/connection-status";
import { zoneChip, zoneName } from "@/lib/format";
import { useNow } from "@/lib/hooks";
import { PAGES, isCurrentPage, type PageInfo } from "@/lib/pages";

const sameStatus = (a: HeaderStatus, b: HeaderStatus) =>
  a.level === b.level && a.label === b.label && a.extra === b.extra && a.detail === b.detail;
const selectStatus = (snapshot: Parameters<typeof deriveStatus>[0]) => deriveStatus(snapshot);

const PAGE_ICON: Record<string, LucideIcon> = {
  "/": Video,
  "/history/": History,
  "/data/": ChartColumn,
  "/setup/": SlidersHorizontal,
  "/system/": Cpu,
};

/** The header stays at the top of the window, and what sticks under it (a page's own toolbar) starts below it. */
export const HEADER_HEIGHT = "3.5rem";

export function Header() {
  const { username, signOut } = useSession();
  const { confirmLeave } = useUnsaved();
  const status = useLiveSelector(selectStatus, sameStatus);
  return (
    <>
      <header className="sticky top-0 z-40 border-b border-edge bg-surface">
        <div className="mx-auto flex h-14 max-w-(--shell) items-center gap-3 px-4 sm:px-6">
          <div className="flex items-center gap-2.5">
            <Logo className="size-8 text-accent" />
            <p className="hidden text-lg font-semibold sm:block">TrafficPi</p>
          </div>
          {/* On a phone the pages are in a bar at the bottom of the screen (below), where a thumb reaches. */}
          <div className="hidden flex-1 justify-center sm:flex">
            <PageNav variant="top" />
          </div>
          <div className="ml-auto flex min-w-0 items-center gap-2 sm:ml-0 sm:gap-3">
            <StatusPill status={status} />
            <ZoneChip />
            <span className="hidden text-sm text-ink-2 sm:inline">{username}</span>
            <Button
              variant="ghost"
              size="sm"
              className="hidden sm:inline-flex"
              onClick={() => {
                if (confirmLeave()) void signOut();
              }}
            >
              <LogOut className="size-4" aria-hidden="true" />
              Sign out
            </Button>
            <AccountMenu username={username} onSignOut={() => confirmLeave() && void signOut()} />
          </div>
        </div>
      </header>
      <PageNav variant="bottom" />
    </>
  );
}

/** What the connection and the Pi are doing, as one pill: a pulsing dot and LIVE while all is well, an icon and the trouble otherwise. */
function StatusPill({ status }: { status: HeaderStatus }) {
  const live = status.level === "good";
  return (
    <span
      className={cn(
        "inline-flex min-w-0 items-center gap-2 rounded-full border py-1 pl-2.5 pr-3 text-sm text-ink",
        status.level === "warning" && "border-warning/60 bg-[color-mix(in_oklab,var(--warning)_16%,var(--surface))]",
        status.level === "critical" && "border-critical/60 bg-[color-mix(in_oklab,var(--critical)_14%,var(--surface))]",
        status.level !== "warning" && status.level !== "critical" && "border-edge bg-sunken",
      )}
      title={status.detail}
    >
      {status.level === "good" ? (
        <span className="relative flex size-2.5 shrink-0" aria-hidden="true">
          <span className="absolute inline-flex size-full animate-ping rounded-full bg-good opacity-60" />
          <span className="relative inline-flex size-2.5 rounded-full bg-good" />
        </span>
      ) : status.level === "warning" ? (
        <TriangleAlert className="size-4 shrink-0 text-warning" aria-hidden="true" />
      ) : status.level === "critical" ? (
        <CircleX className="size-4 shrink-0 text-critical" aria-hidden="true" />
      ) : (
        <CircleDashed className="size-4 shrink-0 text-ink-3" aria-hidden="true" />
      )}
      {/* Only the state is announced to a screen reader; the changing frame rate is not. */}
      <span role="status" className={cn("truncate", live && "font-semibold uppercase tracking-wide")}>
        {status.label}
      </span>
      {status.extra && <span className="shrink-0 tabular-nums text-ink-2">{status.extra}</span>}
    </span>
  );
}

/** The zone every time on the page is in, said once and small ("ET"); its full name is in the tooltip. */
function ZoneChip() {
  const zone = useStats().today?.timezone;
  const now = useNow(60_000);
  if (!zone || now === 0) return null;
  return (
    <span
      className="inline-flex h-6 shrink-0 items-center rounded-md border border-edge px-1.5 text-xs font-medium text-ink-2"
      title={`Times are in ${zoneName(zone)}`}
    >
      <span className="sr-only">Time zone: </span>
      {zoneChip(zone, now)}
    </span>
  );
}

/** On a phone, the name and Sign out are behind one button, so that the header is one row. */
function AccountMenu({ username, onSignOut }: { username: string | null; onSignOut: () => void }) {
  const [open, setOpen] = useState(false);
  const wrapper = useRef<HTMLDivElement>(null);
  const panelId = useId();
  useEffect(() => {
    if (!open) return;
    const onPress = (event: PointerEvent) => {
      if (!(event.target instanceof Node) || !wrapper.current?.contains(event.target)) setOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPress);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPress);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);
  return (
    <div ref={wrapper} className="relative sm:hidden">
      <button
        type="button"
        aria-label="Account"
        aria-expanded={open}
        aria-controls={panelId}
        onClick={() => setOpen((was) => !was)}
        className="grid size-10 place-items-center rounded-lg text-ink-2 transition-colors hover:bg-sunken hover:text-ink"
      >
        <UserRound className="size-5" aria-hidden="true" />
      </button>
      {open && (
        <div id={panelId} className="absolute right-0 top-full z-50 mt-2 w-48 rounded-lg border border-edge bg-surface p-2 shadow-lg">
          {username && <p className="truncate px-2 py-1.5 text-sm text-ink-2">Signed in as {username}</p>}
          <Button variant="ghost" className="w-full justify-start" onClick={onSignOut}>
            <LogOut className="size-4" aria-hidden="true" />
            Sign out
          </Button>
        </div>
      )}
    </div>
  );
}

/** The pages: a row of links in the header, or, on a phone, a bar of icons fixed to the bottom of the window. */
function PageNav({ variant }: { variant: "top" | "bottom" }) {
  const pathname = usePathname();
  const { confirmLeave } = useUnsaved();
  const bottom = variant === "bottom";
  const link = (page: PageInfo) => {
    const current = isCurrentPage(pathname, page.href);
    const Icon = PAGE_ICON[page.href];
    return (
      <li key={page.href} className={bottom ? "flex" : undefined}>
        <Link
          href={page.href}
          aria-current={current ? "page" : undefined}
          onClick={(event) => {
            if (!current && !confirmLeave()) event.preventDefault();
          }}
          className={cn(
            "transition-colors",
            bottom
              ? "flex w-full flex-col items-center justify-center gap-0.5 py-2 text-xs font-medium"
              : "inline-flex h-9 items-center rounded-lg px-3 text-sm font-medium",
            bottom ? (current ? "text-accent" : "text-ink-2") : current ? "bg-sunken text-ink" : "text-ink-2 hover:bg-sunken hover:text-ink",
          )}
        >
          {bottom && Icon && <Icon className="size-6" aria-hidden="true" />}
          {page.label}
        </Link>
      </li>
    );
  };
  return (
    <nav
      aria-label="Pages"
      className={bottom ? "fixed inset-x-0 bottom-0 z-40 border-t border-edge bg-surface pb-[env(safe-area-inset-bottom)] sm:hidden" : undefined}
    >
      <ul className={bottom ? "mx-auto grid max-w-md grid-cols-5" : "-mx-1 flex gap-1"}>{PAGES.map(link)}</ul>
    </nav>
  );
}
