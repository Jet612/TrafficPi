import type { Metadata } from "next";
import { DriftCard } from "@/components/drift-card";
import { HealthCard } from "@/components/health-card";
import { SystemCard } from "@/components/system-card";

export const metadata: Metadata = { title: "System" };

export default function SystemPage() {
  return (
    <div className="grid gap-4">
      <h1 className="sr-only">System</h1>
      <SystemCard />
      <HealthCard />
      <DriftCard />
    </div>
  );
}
