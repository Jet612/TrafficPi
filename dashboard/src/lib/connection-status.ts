import type { LiveSnapshot } from "./live";
import { describeThrottle } from "./severity";
import type { StatusLevel } from "@/components/ui/status";

export interface HeaderStatus {
  level: StatusLevel;
  /** The state, in words. This is what a screen reader announces when it changes. */
  label: string;
  /** Something that changes all the time, such as the frame rate: shown, but never announced. */
  extra?: string;
  /** Words for a tooltip or a second line, such as the camera's error. */
  detail?: string;
}

/** "the CPU speed is capped; it is running hot" as a sentence. */
const sentence = (flags: readonly string[]): string => {
  const text = flags.map(describeThrottle).join("; ");
  return `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;
};

/**
 * What the pill in the header says, from the state of the live socket and of the Pi: live and how
 * fast, or what is wrong. The Pi slowing itself down (too hot, or a weak supply) turns it amber, so
 * that the page does not say all is well while the board is struggling.
 */
export function deriveStatus(snapshot: LiveSnapshot): HeaderStatus {
  const { connection, state, visionOnline } = snapshot;
  if (connection === "closed") return { level: "neutral", label: "Not connected" };
  if (connection === "paused") return { level: "neutral", label: "Paused while the page is hidden" };
  if (connection === "connecting") return { level: "neutral", label: "Connecting…" };
  if (connection === "retrying") {
    return { level: "critical", label: "Connection lost", detail: "Trying to reach the Pi again." };
  }
  if (!visionOnline) {
    return { level: "critical", label: "Vision service offline", detail: "The service that watches the camera is not running." };
  }
  if (!state) return { level: "neutral", label: "Waiting for data…" };
  if (!state.camera.ok) {
    return { level: "critical", label: "Camera offline", detail: state.camera.error ?? undefined };
  }
  const fps = `${state.fps.toFixed(0)} fps`;
  const trouble = state.system.throttled_now;
  if (trouble.length > 0) {
    // Only a weak supply is a different trouble from the board slowing itself down.
    const power = trouble.every((flag) => flag === "under-voltage");
    return { level: "warning", label: power ? "Weak power" : "Throttled", extra: fps, detail: sentence(trouble) };
  }
  return { level: "good", label: "Live", extra: fps };
}

/** Whether boxes from the socket describe the picture being shown right now. */
export function overlayIsCurrent(snapshot: LiveSnapshot): boolean {
  return snapshot.connection === "open" && snapshot.visionOnline && snapshot.state?.camera.ok === true;
}
