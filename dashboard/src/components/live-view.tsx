"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { VideoOff } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { useLiveClient, useLiveSelector } from "@/components/live-provider";
import { deriveStatus, overlayIsCurrent } from "@/lib/connection-status";
import { usePersistentState } from "@/lib/hooks";
import type { LiveSnapshot } from "@/lib/live";
import {
  DEFAULT_OVERLAY,
  drawOverlay,
  readOverlayColors,
  updateTrails,
  type Canvas2D,
  type OverlayOptions,
  type Trail,
} from "@/lib/overlay";

interface Prefs extends OverlayOptions {
  video: boolean;
}

const DEFAULT_PREFS: Prefs = { video: true, ...DEFAULT_OVERLAY };

function subscribeVisibility(onChange: () => void) {
  document.addEventListener("visibilitychange", onChange);
  return () => document.removeEventListener("visibilitychange", onChange);
}

/** False while the tab is hidden: the preview stream is then closed, so it costs the Pi nothing. */
function usePageVisible(): boolean {
  return useSyncExternalStore(subscribeVisibility, () => !document.hidden, () => true);
}

/** Why there is no picture, or null when there is one. */
function selectNoPicture(snapshot: LiveSnapshot): { label: string; detail?: string } | null {
  const status = deriveStatus(snapshot);
  return status.level === "good" ? null : { label: status.label, detail: status.detail };
}
const sameReason = (a: ReturnType<typeof selectNoPicture>, b: ReturnType<typeof selectNoPicture>) =>
  a === b || (a !== null && b !== null && a.label === b.label && a.detail === b.detail);

export function LiveView() {
  const client = useLiveClient();
  const [stored, setPrefs] = usePersistentState<Partial<Prefs>>("trafficpi.live-view", DEFAULT_PREFS);
  // Settings saved before a switch existed lack it: the default fills the gap.
  const prefs = useMemo<Prefs>(() => ({ ...DEFAULT_PREFS, ...stored }), [stored]);
  const noPicture = useLiveSelector(selectNoPicture, sameReason);
  const visible = usePageVisible();
  const frameRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const trails = useRef(new Map<number, Trail>());
  const [attempt, setAttempt] = useState(0);
  const [streamFailed, setStreamFailed] = useState(false);
  const [aspect, setAspect] = useState(16 / 9);

  // A stream that failed (the vision service restarted, say) is tried again after a moment.
  useEffect(() => {
    if (!streamFailed) return;
    const timer = setTimeout(() => {
      setStreamFailed(false);
      setAttempt((n) => n + 1);
    }, 3000);
    return () => clearTimeout(timer);
  }, [streamFailed]);

  // The boxes are painted straight from the socket, outside React: ten updates a second would
  // otherwise re-render this whole card ten times a second.
  useEffect(() => {
    const canvas = canvasRef.current;
    const frame = frameRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !frame || !context) return;
    const ctx = context as unknown as Canvas2D;
    const drawn = trails.current;
    let size = { width: 0, height: 0 };
    let colors = readOverlayColors();
    let pending = 0;
    let lastState: LiveSnapshot["state"] = null;

    const paint = () => {
      pending = 0;
      const snapshot = client.getSnapshot();
      const current = overlayIsCurrent(snapshot);
      if (!current) drawn.clear();
      const state = snapshot.state;
      const scene = { lines: state?.lines ?? [], zones: state?.zones ?? [], tracks: current && state ? state.tracks : [] };
      drawOverlay(ctx, size, scene, prefs, drawn, colors);
    };
    const schedule = () => {
      if (!pending) pending = requestAnimationFrame(paint);
    };
    const resize = () => {
      const box = frame.getBoundingClientRect();
      const ratio = window.devicePixelRatio || 1;
      canvas.width = Math.round(box.width * ratio);
      canvas.height = Math.round(box.height * ratio);
      context.setTransform(ratio, 0, 0, ratio, 0, 0);
      size = { width: box.width, height: box.height };
      paint();
    };
    const onLive = () => {
      const state = client.getSnapshot().state;
      if (state !== lastState) {
        lastState = state;
        if (state) updateTrails(drawn, state.tracks, Date.now());
      }
      schedule();
    };

    resize();
    const unsubscribe = client.subscribe(onLive);
    const observer = new ResizeObserver(resize);
    observer.observe(frame);
    const scheme = window.matchMedia("(prefers-color-scheme: dark)");
    const onScheme = () => {
      colors = readOverlayColors();
      schedule();
    };
    scheme.addEventListener("change", onScheme);
    return () => {
      unsubscribe();
      observer.disconnect();
      scheme.removeEventListener("change", onScheme);
      cancelAnimationFrame(pending);
    };
  }, [client, prefs]);

  const showStream = prefs.video && visible && noPicture === null && !streamFailed;
  const placeholder = !prefs.video
    ? { label: "Video is off", detail: "Boxes, lines and zones still show what the camera sees." }
    : !visible
      ? { label: "Paused while the page is hidden" }
      : streamFailed
        ? { label: "Reconnecting to the video…" }
        : noPicture;

  const set = (change: Partial<Prefs>) => setPrefs({ ...prefs, ...change });

  return (
    <Card>
      <CardHeader>
        <CardTitle>Live view</CardTitle>
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2" role="group" aria-label="What to show">
          <Switch label="Video" checked={prefs.video} onChange={(video) => set({ video })} />
          <Switch label="Boxes" checked={prefs.boxes} onChange={(boxes) => set({ boxes })} />
          <Switch label="Labels" checked={prefs.labels} onChange={(labels) => set({ labels })} />
          <Switch label="Trails" checked={prefs.trails} onChange={(trails) => set({ trails })} />
          <Switch label="Lines" checked={prefs.lines} onChange={(lines) => set({ lines })} />
          <Switch label="Zones" checked={prefs.zones} onChange={(zones) => set({ zones })} />
        </div>
      </CardHeader>
      <CardBody>
        <div ref={frameRef} className="relative w-full overflow-hidden rounded-lg bg-[#0b0b0b]" style={{ aspectRatio: aspect }}>
          {showStream && (
            // An <img> is how a browser plays an MJPEG stream, and Next's image component cannot.
            // eslint-disable-next-line @next/next/no-img-element
            <img
              key={attempt}
              src={`/api/preview.mjpg?attempt=${attempt}`}
              alt="Live view of the street"
              className="absolute inset-0 size-full object-fill"
              onLoad={(event) => {
                const { naturalWidth, naturalHeight } = event.currentTarget;
                if (naturalWidth > 0 && naturalHeight > 0) setAspect(naturalWidth / naturalHeight);
              }}
              onError={() => setStreamFailed(true)}
            />
          )}
          {placeholder && (
            <div className="absolute inset-0 grid place-items-center p-6 text-center">
              <div className="max-w-xs text-[#c3c2b7]">
                <VideoOff className="mx-auto mb-2 size-7" aria-hidden="true" />
                <p className="font-medium text-white">{placeholder.label}</p>
                {placeholder.detail && <p className="mt-1 text-sm">{placeholder.detail}</p>}
              </div>
            </div>
          )}
          <canvas ref={canvasRef} aria-hidden="true" className="pointer-events-none absolute inset-0 size-full" />
        </div>
        <LiveFooter />
      </CardBody>
    </Card>
  );
}

const selectFooter = (snapshot: LiveSnapshot) => ({
  tracked: snapshot.state?.tracks.filter((track) => track.confirmed).length ?? 0,
  detector: snapshot.state?.detector ?? "",
  profile: snapshot.state?.profile_version ?? 0,
});
const sameFooter = (a: ReturnType<typeof selectFooter>, b: ReturnType<typeof selectFooter>) =>
  a.tracked === b.tracked && a.detector === b.detector && a.profile === b.profile;

function LiveFooter() {
  const { tracked, detector } = useLiveSelector(selectFooter, sameFooter);
  return (
    <p className="mt-3 text-sm text-ink-2">
      {tracked === 0 ? "No vehicles in view" : `${tracked} tracked`}
      {detector && <> &middot; model {detector}</>}
    </p>
  );
}
