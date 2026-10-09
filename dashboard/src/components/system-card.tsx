"use client";

import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Meter } from "@/components/ui/meter";
import { StatTile } from "@/components/ui/stat";
import { StatusBadge } from "@/components/ui/status";
import { useLiveSelector } from "@/components/live-provider";
import { api } from "@/lib/api";
import { formatCount, formatDuration, formatMegabytes, formatPercent } from "@/lib/format";
import { usePoll } from "@/lib/hooks";
import type { LiveSnapshot } from "@/lib/live";
import { cpuSeverity, describeThrottle, diskSeverity, temperatureSeverity } from "@/lib/severity";

const selectFps = (snapshot: LiveSnapshot) => (snapshot.state?.camera.ok ? Math.round(snapshot.state.fps) : null);

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

  return (
    <Card>
      <CardHeader>
        <CardTitle>System</CardTitle>
        {data && (
          <span className="text-sm text-ink-2">
            {data.vision.detector ? `model ${data.vision.detector}` : "vision service not running"}
            {` · ${formatCount(data.events_total)} counts stored`}
          </span>
        )}
      </CardHeader>
      <CardBody>
        {status.loaded && data === null && <p className="mb-3 text-sm text-ink">System details could not be loaded. Trying again.</p>}
        <dl className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <StatTile label="Frame rate" value={fps === null ? "–" : `${fps} fps`} note={fps === null ? "no video" : "camera to counts"} />
          <StatTile
            label="CPU"
            value={cpu === null ? "–" : formatPercent(cpu)}
            note={system?.cpu_cores != null ? `${system.cpu_cores.toFixed(2)} cores used by the camera loop` : "whole machine"}
          >
            {cpu !== null && <Meter label="CPU in use" value={cpu} severity={cpuSeverity(cpu)} valueText={formatPercent(cpu)} />}
          </StatTile>
          <StatTile label="Memory" value={system?.rss_mb != null ? formatMegabytes(system.rss_mb) : "–"} note="held by the camera loop" />
          <StatTile label="Temperature" value={temp === null ? "–" : `${temp.toFixed(0)} °C`} note="processor">
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
            label="Uptime"
            value={data?.vision.uptime_s != null ? formatDuration(data.vision.uptime_s) : "–"}
            note="since the camera loop started"
          />
        </dl>
        {now.length > 0 && (
          <ul className="mt-4 flex flex-wrap gap-2" aria-label="Problems right now">
            {now.map((flag) => (
              <li key={flag}>
                <StatusBadge level="warning">
                  {flag}: {describeThrottle(flag)}
                </StatusBadge>
              </li>
            ))}
          </ul>
        )}
        {before.length > 0 && (
          <p className="mt-3 text-sm text-ink-2">Since the Pi started: {before.map(describeThrottle).join("; ")}.</p>
        )}
      </CardBody>
    </Card>
  );
}
