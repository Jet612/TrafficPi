import type { Metadata } from "next";
import { EventsCard } from "@/components/events-card";
import { LiveView } from "@/components/live-view";
import { TodayCard } from "@/components/today-card";

export const metadata: Metadata = { title: "Live" };

export default function LivePage() {
  return (
    <div className="grid gap-4 lg:grid-cols-12">
      <h1 className="sr-only">Live</h1>
      <div className="min-w-0 lg:col-span-8">
        <LiveView />
      </div>
      <div className="flex min-w-0 flex-col gap-4 lg:col-span-4">
        <TodayCard />
        <EventsCard />
      </div>
    </div>
  );
}
