import type { Metadata } from "next";
import { SettingsCard } from "@/components/settings-card";
import { ZoneEditor } from "@/components/zone-editor";

export const metadata: Metadata = { title: "Setup" };

export default function SetupPage() {
  return (
    <div className="grid gap-4">
      <h1 className="sr-only">Setup</h1>
      <ZoneEditor />
      <SettingsCard />
    </div>
  );
}
