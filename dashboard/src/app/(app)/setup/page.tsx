import type { Metadata } from "next";
import { MailCard } from "@/components/mail-card";
import { PageNav } from "@/components/page-nav";
import { SettingsCard } from "@/components/settings-card";
import { StopHoursCard } from "@/components/stop-hours-card";
import { ZoneEditor } from "@/components/zone-editor";

export const metadata: Metadata = { title: "Setup" };

const SECTIONS = [
  { id: "lines-and-zones", label: "Lines & zones" },
  { id: "stop-sign-hours", label: "Stop-sign hours" },
  { id: "settings", label: "General & storage" },
  { id: "daily-email", label: "Daily email" },
] as const;

export default function SetupPage() {
  return (
    <div className="grid grid-cols-1 gap-4">
      <h1 className="sr-only">Setup</h1>
      <PageNav sections={SECTIONS} />
      {/* The cards keep their own code; the page gives each an address to jump to, below the header and the strip of links. */}
      <div id="lines-and-zones" className="scroll-mt-28">
        <ZoneEditor />
      </div>
      <div id="stop-sign-hours" className="scroll-mt-28">
        <StopHoursCard />
      </div>
      <div id="settings" className="scroll-mt-28">
        <SettingsCard />
      </div>
      <div id="daily-email" className="scroll-mt-28">
        <MailCard />
      </div>
    </div>
  );
}
