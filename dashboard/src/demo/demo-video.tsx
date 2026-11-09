"use client";

import { useEffect, useRef } from "react";
import { registerVideo } from "./playback";

export const DEMO_VIDEO_URL = "/demo/street.mp4";

/**
 * The live picture in the demo: a recorded loop of the made-up street. The live messages are
 * chosen by where this video is (`playbackPosition`), so the boxes stay on the vehicles.
 */
export function DemoVideo({ onAspect, onFailed }: { onAspect: (aspect: number) => void; onFailed: () => void }) {
  const ref = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    const video = ref.current;
    if (!video) return;
    registerVideo(video);
    return () => registerVideo(null);
  }, []);

  return (
    <video
      ref={ref}
      src={DEMO_VIDEO_URL}
      muted
      loop
      autoPlay
      playsInline
      aria-label="Live view of the made-up street (a recording)"
      className="absolute inset-0 size-full object-fill"
      onLoadedMetadata={(event) => {
        const { videoWidth, videoHeight } = event.currentTarget;
        if (videoWidth > 0 && videoHeight > 0) onAspect(videoWidth / videoHeight);
      }}
      onError={onFailed}
    />
  );
}
