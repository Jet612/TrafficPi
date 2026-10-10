import { Download } from "lucide-react";
import { button } from "@/components/ui/button";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { exportUrl, type ExportKind } from "@/lib/export";

const FILES: readonly { kind: ExportKind; title: string; text: string }[] = [
  { kind: "counts", title: "Vehicles counted", text: "One row for each vehicle counted: when, which line, which way, what kind, and its speed if it has one." },
  {
    kind: "passes",
    title: "Passes at stop signs",
    text: "One row for each pass: when, which stop sign, what kind, what it did, how long it stood still and how fast it went, and its review (false alarms are in it, marked).",
  },
];

/** The two files of the Data page: what the counting lines counted and what vehicles did at the stop signs, as CSV for a spreadsheet. */
export function ExportCard({ days, range }: { days: number; range: string }) {
  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Download as CSV</CardTitle>
          <CardSubtitle>{range}: the days the page covers. Times are in UTC and in the camera&rsquo;s time zone; speeds are in metres a second.</CardSubtitle>
        </div>
      </CardHeader>
      <CardBody>
        <ul className="grid gap-4 sm:grid-cols-2">
          {FILES.map((file) => (
            <li key={file.kind} className="flex flex-col items-start gap-2 rounded-lg bg-sunken p-4" data-testid={`export-${file.kind}`}>
              <p className="text-sm font-medium text-ink">{file.title}</p>
              <p className="text-sm text-ink-2">{file.text}</p>
              <a href={exportUrl(file.kind, days)} download className={button({ variant: "secondary", size: "sm" })}>
                <Download className="size-4" aria-hidden="true" />
                Download {file.title.toLowerCase()}
              </a>
            </li>
          ))}
        </ul>
      </CardBody>
    </Card>
  );
}
