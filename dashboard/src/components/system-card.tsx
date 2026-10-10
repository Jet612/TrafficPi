"use client";

import { useId } from "react";
import { Card, CardBody, CardHeader, CardSubtitle, CardTitle } from "@/components/ui/card";
import { Meter } from "@/components/ui/meter";
import { StatTile } from "@/components/ui/stat";
import { StatusBadge } from "@/components/ui/status";
import { useLiveSelector } from "@/components/live-provider";
import { api } from "@/lib/api";
import { formatDuration, formatMegabytes, formatPercent } from "@/lib/format";
import { usePoll } from "@/lib/hooks";
import type { LiveSnapshot } from "@/lib/live";
import { acceleratorFacts, backupLevel, backupText, chipName, dataFacts, latencyText, memoryTestText } from "@/lib/machine";
import { cpuSeverity, describeThrottle, diskSeverity, temperatureSeverity, temperatureWord } from "@/lib/severity";

const selectFps = (snapshot: LiveSnapshot) => (snapshot.state?.camera.ok ? Math.round(snapshot.state.fps) : null);

const capitalised = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;

/** A few tiles under a heading of their own: the readings are grouped by what they are about, not laid out in one run of ten. */
function Group({ title, children }: { title: string; children: React.ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <h3 id={id} className="mb-2 text-sm font-semibold text-ink">
        {title}
      </h3>
      <dl className="grid grid-cols-[repeat(auto-fill,minmax(11.5rem,1fr))] gap-3">{children}</dl>
    </section>
  );
}

/** Secondary facts of a tile as a small list of label and value, instead of a long note that runs them together. */
function Facts({ items }: { items: readonly { label: string; value: string }[] }) {
  if (items.length === 0) return null;
  return (
    <span className="grid gap-0.5 text-sm">
      {items.map((item) => (
        <span key={item.label} className="flex justify-between gap-3">
          <span className="text-ink-2">{item.label}</span>
          <span className="min-w-0 truncate text-right tabular-nums text-ink">{item.value}</span>
        </span>
      ))}
    </span>
  );
}

/** What the machine is doing now: the picture pipeline, the host it runs on, and what it has stored. */
export function SystemCard() {
  const status = usePoll((signal) => api.status(signal), 5000);
  const fps = useLiveSelector(selectFps);
  const data = status.data;
  const system = data?.vision.system;
  const temp = system?.soc_temp_c ?? null;
  const cpu = system?.cpu_percent ?? null;
  const disk = data?.disk;
  const now = system?.throttled_now ?? [];
  const before = system?.throttled_since_boot ?? [];
  const accelerator = data?.vision.accelerator;
  const latency = accelerator ? latencyText(accelerator) : null;
  const backup = data ? backupText(data.last_backup, data.timezone, data.now_ms) : null;
  const backupState = data ? backupLevel(data.last_backup, data.now_ms) : null;
  const memory = data ? memoryTestText(data.memory_test, data.timezone, data.now_ms) : null;

  return (
    <Card>
      <CardHeader>
        <div>
          <CardTitle>Right now</CardTitle>
          <CardSubtitle>Readings from the camera loop and the Pi, renewed every few seconds.</CardSubtitle>
        </div>
      </CardHeader>
      <CardBody className="grid gap-6">
        {status.loaded && data === null && <p className="text-sm text-ink">System details could not be loaded. Trying again.</p>}

        <Group title="Pipeline">
          <StatTile label="Frame rate" value={fps === null ? "–" : `${fps} fps`} note={fps === null ? "no video" : "camera to counts"} />
          <StatTile
            label="Accelerator"
            value={data === null ? "–" : data.vision.status === "unreachable" ? "–" : chipName(accelerator?.architecture)}
            note={data !== null && data.vision.status === "unreachable" ? "vision service not running" : undefined}
            wrap
          >
            {accelerator && <Facts items={acceleratorFacts(accelerator)} />}
          </StatTile>
          <StatTile label="Latency" value={latency?.value ?? "–"} note={latency?.note ?? (latency === null ? "not timed yet" : undefined)} />
        </Group>

        <Group title="Host">
          <StatTile
            label="CPU, whole Pi"
            value={cpu === null ? "–" : formatPercent(cpu)}
            note={system?.cpu_cores != null ? `Camera loop: ${system.cpu_cores.toFixed(2)} cores` : undefined}
          >
            {cpu !== null && <Meter label="CPU in use" value={cpu} severity={cpuSeverity(cpu)} valueText={formatPercent(cpu)} />}
          </StatTile>
          <StatTile label="Memory" value={system?.rss_mb != null ? formatMegabytes(system.rss_mb) : "–"} note="held by the camera loop" />
          <StatTile label="Temperature" value={temp === null ? "–" : `${temp.toFixed(0)} °C`} note={temp === null ? undefined : temperatureWord(temp)}>
            {temp !== null && <Meter label="Processor temperature" min={30} max={85} value={temp} severity={temperatureSeverity(temp)} valueText={`${temp.toFixed(0)} degrees`} />}
          </StatTile>
          <StatTile
            label="Disk used"
            value={disk ? formatPercent(disk.used_percent, 1) : "–"}
            note={disk ? `${disk.free_gb.toFixed(0)} GB free of ${disk.total_gb.toFixed(0)}` : undefined}
          >
            {disk && <Meter label="Disk in use" value={disk.used_percent} severity={diskSeverity(disk.used_percent)} valueText={formatPercent(disk.used_percent, 1)} />}
          </StatTile>
          <StatTile
            label="Service uptime"
            value={data?.vision.uptime_s != null ? formatDuration(data.vision.uptime_s) : "–"}
            note={data ? `Dashboard server ${formatDuration(data.api_uptime_s)}` : undefined}
          />
        </Group>

        {data && (
          <Group title="Storage">
            <StatTile label="Data on disk" value={formatMegabytes(data.data.database_mb + data.data.evidence_mb + data.data.backups_mb)} wrap className="col-span-2">
              <Facts items={dataFacts(data.data, data.events_total)} />
            </StatTile>
            <StatTile label="Last backup" value={backup?.value ?? "–"} note={backup?.note} wrap>
              {backupState === "warning" && <StatusBadge level="warning">older than a day and a half</StatusBadge>}
              {backupState === "none" && <StatusBadge level="warning">not backed up</StatusBadge>}
            </StatTile>
          </Group>
        )}

        {memory?.ok === false && (
          <p>
            <StatusBadge level="critical">The memory test found a problem: this board may have faulty RAM. {memory.note}</StatusBadge>
          </p>
        )}
        {now.length > 0 && (
          <ul className="flex flex-wrap gap-2" aria-label="Problems right now">
            {now.map((flag) => (
              <li key={flag}>
                <StatusBadge level="warning">{capitalised(describeThrottle(flag))}</StatusBadge>
              </li>
            ))}
          </ul>
        )}
        {before.length > 0 && <p className="text-sm text-ink-2">Since boot: {before.map(describeThrottle).join("; ")}.</p>}
      </CardBody>
    </Card>
  );
}
