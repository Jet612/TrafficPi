import type { LiveSnapshot } from "./live";
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

/** What the badge in the header says, from the state of the live socket. */
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
  return { level: "good", label: `Live · ${state.fps.toFixed(0)} fps` };
}

/** Whether boxes from the socket describe the picture being shown right now. */
export function overlayIsCurrent(snapshot: LiveSnapshot): boolean {
  return snapshot.connection === "open" && snapshot.visionOnline && snapshot.state?.camera.ok === true;
}
