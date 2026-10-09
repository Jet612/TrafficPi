"use client";

import { X } from "lucide-react";
import { useEffect, useId, useRef } from "react";
import { cn } from "@/lib/cn";

/**
 * A window over the page, made with the browser's own <dialog>: while it is open it has the keyboard,
 * the page behind it is out of reach, Escape closes it, and focus goes back to what opened it.
 * It is in the page only while it is open: the parent shows it, and is told by `onClose` to stop.
 * (A click beside it does not close it: a drag that starts on a video's slider and ends outside would.)
 */
export function Dialog({
  title,
  onClose,
  children,
  className,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  className?: string;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const dialog = ref.current;
    if (dialog && !dialog.open) dialog.showModal();
  }, []);
  return (
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      onClose={onClose}
      className={cn(
        "m-auto max-h-[calc(100dvh-1.5rem)] w-[min(52rem,calc(100vw-1.5rem))] overflow-auto rounded-xl border border-edge bg-surface p-0 text-ink shadow-xl backdrop:bg-black/60",
        className,
      )}
    >
      <div className="flex items-start justify-between gap-4 border-b border-edge px-4 py-3 sm:px-5">
        <h2 id={titleId} className="min-w-0 text-base font-semibold text-ink">
          {title}
        </h2>
        <button
          type="button"
          aria-label="Close"
          onClick={() => ref.current?.close()}
          className="-m-1 grid size-8 shrink-0 place-items-center rounded-lg text-ink-2 transition-colors hover:bg-sunken hover:text-ink"
        >
          <X className="size-5" aria-hidden="true" />
        </button>
      </div>
      <div className="px-4 py-4 sm:px-5">{children}</div>
    </dialog>
  );
}
