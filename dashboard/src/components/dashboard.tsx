"use client";

import { EventsCard } from "@/components/events-card";
import { Header } from "@/components/header";
import { HourlyChart } from "@/components/hourly-chart";
import { LiveView } from "@/components/live-view";
import { StatsProvider } from "@/components/stats-provider";
import { SystemCard } from "@/components/system-card";
import { TodayCard } from "@/components/today-card";
import { ZoneEditor } from "@/components/zone-editor";

export function Dashboard() {
  return (
    <StatsProvider>
      <a
        href="#main"
        className="sr-only rounded-lg bg-accent px-4 py-2 text-accent-ink focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50"
      >
        Skip to the dashboard
      </a>
      <Header />
      <main id="main" className="mx-auto grid max-w-[1280px] gap-4 px-4 py-4 sm:px-6 sm:py-6 lg:grid-cols-12">
        <div className="min-w-0 lg:col-span-8">
          <LiveView />
        </div>
        <div className="min-w-0 lg:col-span-4">
          <TodayCard />
        </div>
        <div className="min-w-0 lg:col-span-8">
          <HourlyChart />
        </div>
        <div className="min-w-0 lg:col-span-4">
          <EventsCard />
        </div>
        <div className="min-w-0 lg:col-span-12">
          <SystemCard />
        </div>
        <div className="min-w-0 lg:col-span-12">
          <ZoneEditor />
        </div>
      </main>
    </StatsProvider>
  );
}
