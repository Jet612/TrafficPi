import type { Metadata } from "next";
import { CrossingsCard } from "@/components/crossings-card";
import { EventsCard } from "@/components/events-card";
import { LiveView } from "@/components/live-view";
import { TodayCard } from "@/components/today-card";

export const metadata: Metadata = { title: "Live" };

/**
 * The picture and what it counts, in two columns that each end where their cards end (no card is
 * stretched to match its neighbour). On a phone the columns dissolve into one stack and the headline
 * count comes straight after the picture, before the table of lines.
 */
export default function LivePage() {
  return (
    <div className="grid items-start gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
      <h1 className="sr-only">Live</h1>
      <div className="contents lg:grid lg:min-w-0 lg:gap-4">
        <div className="order-1 min-w-0 lg:order-none">
          <LiveView />
        </div>
        <div className="order-3 min-w-0 empty:hidden lg:order-none">
          <CrossingsCard />
        </div>
      </div>
      <div className="contents lg:grid lg:min-w-0 lg:gap-4">
        <div className="order-2 min-w-0 lg:order-none">
          <TodayCard />
        </div>
        <div className="order-4 min-w-0 lg:order-none">
          <EventsCard />
        </div>
      </div>
    </div>
  );
}
