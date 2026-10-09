import type { Metadata } from "next";
import { HourlyChart } from "@/components/hourly-chart";
import { TrailsCard } from "@/components/trails-card";

export const metadata: Metadata = { title: "History" };

export default function HistoryPage() {
  return (
    <div className="grid gap-4">
      <h1 className="sr-only">History</h1>
      <HourlyChart />
      <TrailsCard />
    </div>
  );
}
