"use client";

import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Maximize2, Minimize2, VideoOff } from "lucide-react";
import { Card, CardBody, CardHeader, CardTitle } from "@/components/ui/card";
import { ToggleChip } from "@/components/ui/toggle-chip";
import { useLiveClient, useLiveSelector } from "@/components/live-provider";
import { useSession } from "@/components/session";
import { deriveStatus, overlayIsCurrent } from "@/lib/connection-status";
import { cn } from "@/lib/cn";
import { usePersistentState } from "@/lib/hooks";
import type { LiveSnapshot } from "@/lib/live";
import { STILL_NOTICE, STILL_REFRESH_MS, pictureMode, stillUrl } from "@/lib/remote";
import { useFullscreen } from "@/lib/use-fullscreen";
import type { SpeedUnits } from "@/lib/speed";
import { useSpeedUnits } from "@/lib/use-speed-units";
import { useStopsPaused } from "@/lib/use-stops-paused";
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
  // A Pi that is slowing itself down still has a picture (the header's pill says so): only a link, a
  // service or a camera that is not working takes it away.
  if (overlayIsCurrent(snapshot)) return null;
  const status = deriveStatus(snapshot);
  return { label: status.label, detail: status.detail };
}
const sameReason = (a: ReturnType<typeof selectNoPicture>, b: ReturnType<typeof selectNoPicture>) =>
  a === b || (a !== null && b !== null && a.label === b.label && a.detail === b.detail);

/**
 * A still picture, fetched again every few seconds: what a viewer who comes through Cloudflare sees
 * when the owner has not allowed live video for them. The component is not mounted
 * while the page is hidden, so nothing is fetched then.
 */
function StillPicture({
  attempt,
  onAspect,
  onFailed,
}: {
  attempt: number;
  onAspect: (aspect: number) => void;
  onFailed: () => void;
}) {
  const [n, setN] = useState(0);
  useEffect(() => {
    const timer = setInterval(() => setN((value) => value + 1), STILL_REFRESH_MS);
    return () => clearInterval(timer);
  }, []);
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      key={attempt}
      src={stillUrl(n)}
      alt="Still picture of the street, renewed every few seconds"
      className="absolute inset-0 size-full object-fill"
      onLoad={(event) => {
        const { naturalWidth, naturalHeight } = event.currentTarget;
        if (naturalWidth > 0 && naturalHeight > 0) onAspect(naturalWidth / naturalHeight);
      }}
      onError={onFailed}
    />
  );
}

export function LiveView() {
  const client = useLiveClient();
  const { liveVideo } = useSession();
  const still = pictureMode(liveVideo) === "still";
  const [stored, setPrefs] = usePersistentState<Partial<Prefs>>("trafficpi.live-view", DEFAULT_PREFS);
  // Settings saved before a switch existed lack it: the default fills the gap.
  const prefs = useMemo<Prefs>(() => ({ ...DEFAULT_PREFS, ...stored }), [stored]);
  const noPicture = useLiveSelector(selectNoPicture, sameReason);
  const visible = usePageVisible();
  const stageRef = useRef<HTMLDivElement>(null);
  const frameRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fullscreen = useFullscreen(stageRef);
  const trails = useRef(new Map<number, Trail>());
  // The units a vehicle's speed is written in on its label; the overlay is painted outside React, so it reads them here.
  const unitsRef = useRef<SpeedUnits | undefined>(undefined);
  const speedUnits = useSpeedUnits();
  // Whether stop signs are judging now, for the same reason: a sign outside its hours is drawn faint.
  const pausedRef = useRef(false);
  const stopsPaused = useStopsPaused();
  useEffect(() => {
    unitsRef.current = speedUnits;
    pausedRef.current = stopsPaused;
  });
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
      const scene = {
        lines: state?.lines ?? [],
        zones: state?.zones ?? [],
        stopSigns: state?.stop_signs ?? [],
        units: unitsRef.current,
        stopsPaused: pausedRef.current,
        // A still is seconds old: boxes drawn from the live data would sit beside the vehicles.
        tracks: current && state && !still ? state.tracks : [],
      };
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
  }, [client, prefs, still]);

  const showStream = prefs.video && visible && noPicture === null && !streamFailed;
  const placeholder = !prefs.video
    ? { label: still ? "Picture is off" : "Video is off", detail: "Boxes, lines and zones still show what the camera sees." }
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
      </CardHeader>
      <CardBody>
        {/* The stage is what goes full screen: the picture and the boxes drawn over it, so that both
            stay together. Full screen shows the whole picture, with black bars where it does not fit. */}
        <div
          ref={stageRef}
          data-testid="live-stage"
          role={fullscreen.active ? "dialog" : undefined}
          aria-modal={fullscreen.active || undefined}
          aria-label={fullscreen.active ? "Live view, full screen" : undefined}
          className={cn(fullscreen.active ? "fixed inset-0 z-50 grid place-items-center bg-black" : "relative")}
        >
          <div
            ref={frameRef}
            data-testid="live-frame"
            onDoubleClick={fullscreen.toggle}
            className={cn("relative touch-manipulation overflow-hidden bg-[#0b0b0b]", fullscreen.active ? "" : "w-full rounded-lg")}
            style={{ aspectRatio: aspect, width: fullscreen.active ? `min(100vw, calc(100dvh * ${aspect}))` : undefined }}
          >
            {showStream && still && (
              <StillPicture attempt={attempt} onAspect={setAspect} onFailed={() => setStreamFailed(true)} />
            )}
            {showStream && !still && (
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
            <button
              type="button"
              onClick={fullscreen.toggle}
              onDoubleClick={(event) => event.stopPropagation()}
              aria-label={fullscreen.active ? "Exit full screen" : "Full screen"}
              title={fullscreen.active ? "Exit full screen (Esc)" : "Full screen (or double-click the picture)"}
              className="absolute right-2 top-2 z-10 inline-flex size-9 items-center justify-center rounded-lg bg-black/55 text-white transition-colors hover:bg-black/75"
            >
              {fullscreen.active ? <Minimize2 className="size-5" aria-hidden="true" /> : <Maximize2 className="size-5" aria-hidden="true" />}
            </button>
          </div>
        </div>
        {still && (
          <p data-testid="still-notice" className="mt-3 text-sm text-ink-2">
            {STILL_NOTICE}
          </p>
        )}
        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
          <LiveFooter />
          {/* The picture is one thing and what is drawn over it another: the base layer first, then the five overlays. */}
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="What to show">
            <ToggleChip checked={prefs.video} onChange={(video) => set({ video })}>
              {still ? "Picture" : "Video"}
            </ToggleChip>
            <span className="mx-1 hidden h-5 w-px bg-edge sm:block" aria-hidden="true" />
            <ToggleChip checked={prefs.boxes} onChange={(boxes) => set({ boxes })}>
              Boxes
            </ToggleChip>
            <ToggleChip checked={prefs.labels} onChange={(labels) => set({ labels })}>
              Labels
            </ToggleChip>
            <ToggleChip checked={prefs.trails} onChange={(trails) => set({ trails })}>
              Trails
            </ToggleChip>
            <ToggleChip checked={prefs.lines} onChange={(lines) => set({ lines })}>
              Lines
            </ToggleChip>
            <ToggleChip checked={prefs.zones} onChange={(zones) => set({ zones })}>
              Zones
            </ToggleChip>
          </div>
        </div>
      </CardBody>
    </Card>
  );
}

/** How many vehicles are in view. (The model's name is on the System page, which is where it is about.) */
const selectTracked = (snapshot: LiveSnapshot) => snapshot.state?.tracks.filter((track) => track.confirmed).length ?? 0;

function LiveFooter() {
  const tracked = useLiveSelector(selectTracked);
  return <p className="text-sm text-ink-2">{tracked === 0 ? "No vehicles in view" : `${tracked} in view`}</p>;
}
