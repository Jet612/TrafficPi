"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { ImageOff } from "lucide-react";
import { readOverlayColors } from "@/lib/overlay";
import { paintGrid } from "@/lib/trails-canvas";
import { drawTrailScene, type TrailCanvas, type TrailScene } from "@/lib/trails-draw";

function subscribeScheme(onChange: () => void) {
  const query = window.matchMedia("(prefers-color-scheme: dark)");
  query.addEventListener("change", onChange);
  return () => query.removeEventListener("change", onChange);
}
const isDark = () => window.matchMedia("(prefers-color-scheme: dark)").matches;

/**
 * The camera picture with the paths (or the heat map) and the lines drawn over it. The picture is a
 * plain <img> and the drawing a canvas on top of it, redrawn when what is shown or the size changes.
 */
export function TrailPicture({
  pictureUrl,
  noPictureText,
  onPictureError,
  scene,
  description,
  dimmed,
  children,
}: {
  /** The picture to draw on; null when there is none. */
  pictureUrl: string | null;
  noPictureText: string;
  onPictureError: () => void;
  scene: TrailScene;
  /** What the drawing shows, for anyone who cannot see it. */
  description: string;
  /** Fresh data is on its way: what is drawn is the last that was known. */
  dimmed: boolean;
  /** Messages over the picture. */
  children?: React.ReactNode;
}) {
  const frameRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [aspect, setAspect] = useState(16 / 9);
  const dark = useSyncExternalStore(subscribeScheme, isDark, () => false);

  useEffect(() => {
    const frame = frameRef.current;
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => {
      const box = entry?.contentRect;
      if (box) setSize({ width: box.width, height: box.height });
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, []);

  // `dark` is read here so that a change of theme redraws in the new theme's colours.
  useEffect(() => {
    const canvas = canvasRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context || size.width === 0 || size.height === 0) return;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(size.width * ratio);
    canvas.height = Math.round(size.height * ratio);
    context.setTransform(ratio, 0, 0, ratio, 0, 0);
    drawTrailScene(context as unknown as TrailCanvas, size, scene, readOverlayColors(), paintGrid);
  }, [size, scene, dark]);

  return (
    <div
      ref={frameRef}
      data-testid="trail-picture"
      className="relative w-full select-none overflow-hidden rounded-lg bg-[#0b0b0b]"
      style={{ aspectRatio: aspect }}
    >
      {pictureUrl ? (
        // The picture is a still from the API, so a plain <img> is right (and Next's component is not).
        // eslint-disable-next-line @next/next/no-img-element
        <img
          key={pictureUrl}
          src={pictureUrl}
          alt=""
          draggable={false}
          className="pointer-events-none absolute inset-0 size-full object-fill"
          onLoad={(event) => {
            const { naturalWidth, naturalHeight } = event.currentTarget;
            if (naturalWidth > 0 && naturalHeight > 0) setAspect(naturalWidth / naturalHeight);
          }}
          onError={onPictureError}
        />
      ) : (
        <div className="pointer-events-none absolute inset-0 grid place-items-center p-6 text-center">
          <div className="max-w-xs text-[#c3c2b7]">
            <ImageOff className="mx-auto mb-2 size-7" aria-hidden="true" />
            <p className="text-sm">{noPictureText}</p>
          </div>
        </div>
      )}
      <canvas
        ref={canvasRef}
        data-testid="trail-canvas"
        data-paths={scene.paths.length}
        data-view={scene.view}
        role="img"
        aria-label={description}
        className={`absolute inset-0 size-full transition-opacity ${dimmed ? "opacity-60" : "opacity-100"}`}
      />
      {children}
    </div>
  );
}
