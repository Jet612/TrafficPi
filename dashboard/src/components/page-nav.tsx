"use client";

import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";

export interface PageSection {
  id: string;
  label: string;
}

/**
 * A row of links to the cards of a long page, stuck under the header, with the card that is on screen
 * marked. A page of four tall cards had no way to move between them but the scroll bar. On a phone the
 * row scrolls sideways instead of wrapping.
 */
export function PageNav({ sections, label = "On this page" }: { sections: readonly PageSection[]; label?: string }) {
  const [current, setCurrent] = useState(sections[0]?.id ?? "");

  // The card nearest the top of the window, once it has passed the header, is the one being read.
  useEffect(() => {
    const onScroll = () => {
      const line = window.innerHeight * 0.3;
      let found = sections[0]?.id ?? "";
      for (const section of sections) {
        const box = document.getElementById(section.id)?.getBoundingClientRect();
        if (box && box.top <= line) found = section.id;
      }
      setCurrent(found);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, [sections]);

  return (
    <nav aria-label={label} className="sticky top-14 z-30 -mx-4 border-b border-edge bg-page/95 px-4 backdrop-blur sm:-mx-6 sm:px-6">
      <ul className="flex gap-1 overflow-x-auto py-2 [scrollbar-width:none]">
        {sections.map((section) => (
          <li key={section.id} className="shrink-0">
            <a
              href={`#${section.id}`}
              aria-current={current === section.id ? "location" : undefined}
              className={cn(
                "inline-flex h-8 items-center whitespace-nowrap rounded-lg px-3 text-sm font-medium transition-colors pointer-coarse:h-10",
                current === section.id ? "bg-accent-wash text-ink" : "text-ink-2 hover:bg-sunken hover:text-ink",
              )}
            >
              {section.label}
            </a>
          </li>
        ))}
      </ul>
    </nav>
  );
}
