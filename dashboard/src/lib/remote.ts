/**
 * How the camera is shown to a viewer who comes through Cloudflare.
 *
 * Cloudflare's terms let it limit a customer who uses its network for video without a paid plan,
 * and they do not say whether a Tunnel counts, so the server holds the continuous live stream back
 * from such viewers unless the owner switched it on. They get a still picture instead, renewed
 * every few seconds, with the lines and zones over it (the boxes would be seconds behind it).
 */

/** How often the still picture is fetched again, in milliseconds. */
export const STILL_REFRESH_MS = 3000;

/** The address of the still for the n-th refresh: the number keeps the browser from reusing the last. */
export function stillUrl(n: number): string {
  return `/api/snapshot.jpg?n=${n}`;
}

/** What this viewer is shown. The server decides (`live_video` in /api/auth/me); this only names it. */
export type PictureMode = "stream" | "still";

export function pictureMode(liveVideo: boolean): PictureMode {
  return liveVideo ? "stream" : "still";
}

/** The words under a still picture. */
export const STILL_NOTICE =
  "Live video is off for people who come through Cloudflare, so this is a still picture, renewed every " +
  `${STILL_REFRESH_MS / 1000} seconds, with no boxes on it (it is seconds behind the traffic). Cloudflare's terms limit video on its network. ` +
  "The owner can switch live video on for them in Setup, under Settings.";
