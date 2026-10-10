import type { Metadata } from "next";
import { MailCard } from "@/components/mail-card";
import { SettingsCard } from "@/components/settings-card";
import { StopHoursCard } from "@/components/stop-hours-card";
import { ZoneEditor } from "@/components/zone-editor";

export const metadata: Metadata = { title: "Setup" };

export default function SetupPage() {
  return (
    <div className="grid gap-4">
      <h1 className="sr-only">Setup</h1>
      <ZoneEditor />
      <StopHoursCard />
      <SettingsCard />
      <MailCard />
    </div>
  );
}
