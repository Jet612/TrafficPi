/**
 * Turns the dashboard into the static demo before it starts: the page's clock is moved to the
 * recording's, requests to `/api/` are answered by `DemoServer`, and the live socket by
 * `DemoSocket`. Pictures, clips and CSV files under `/api/` are real files beside the site, so the
 * browser fetches them as usual.
 */
import { DemoServer } from "./server";
import { DemoSocket } from "./socket";
import { shiftClock, startPlayback } from "./playback";
import { RECORDING_FORMAT, type LiveRecording, type Recording } from "./recording";

/** A path under /api/ that ends in a file name (snapshot.jpg, clip.mp4, counts.csv) is a file on the site. */
const isFile = (pathname: string) => /\.[a-z0-9]+$/i.test(pathname);

export function installDemo(recordedAt: number | null): void {
  if (typeof window === "undefined") return;
  if (recordedAt !== null) shiftClock(recordedAt);
  startPlayback();

  const realFetch = window.fetch.bind(window);
  const loadJson = async (url: string): Promise<unknown> => {
    const response = await realFetch(url);
    if (!response.ok) throw new Error(`${url}: ${response.status}`);
    return response.json();
  };
  const ready = Promise.all([loadJson("/demo/recording.json"), loadJson("/demo/live.json")]).then(([recording, live]) => {
    const rec = recording as Recording;
    if (rec.format !== RECORDING_FORMAT) throw new Error(`the recording is format ${rec.format}, this page reads ${RECORDING_FORMAT}`);
    return { server: new DemoServer(rec, loadJson), live: live as LiveRecording };
  });
  ready.catch((error: unknown) => console.error("The demo's recording could not be loaded:", error));

  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const request = input instanceof Request ? input : null;
    const url = new URL(request?.url ?? String(input), window.location.href);
    if (url.origin !== window.location.origin || !url.pathname.startsWith("/api/") || isFile(url.pathname)) {
      return realFetch(input, init);
    }
    const signal = init?.signal ?? request?.signal;
    if (signal?.aborted) throw new DOMException("The request was aborted", "AbortError");
    const method = (init?.method ?? request?.method ?? "GET").toUpperCase();
    const text = typeof init?.body === "string" ? init.body : request ? await request.text() : "";
    let server: DemoServer;
    try {
      server = (await ready).server;
    } catch {
      return new Response(JSON.stringify({ detail: "The demo's recording could not be loaded" }), { status: 503 });
    }
    const reply = await server.handle(method, url, text === "" ? undefined : JSON.parse(text));
    if (signal?.aborted) throw new DOMException("The request was aborted", "AbortError");
    if (reply.body === undefined) return new Response(null, { status: reply.status });
    return new Response(JSON.stringify(reply.body), { status: reply.status, headers: { "Content-Type": "application/json" } });
  };

  const RealWebSocket = window.WebSocket;
  // A class whose constructor returns another object: `new WebSocket(url)` gives the stand-in for
  // the live socket and a real socket for anything else.
  class DemoWebSocket {
    constructor(url: string | URL, protocols?: string | string[]) {
      if (new URL(String(url), window.location.href).pathname === "/api/live") {
        return new DemoSocket(ready) as unknown as DemoWebSocket;
      }
      return new RealWebSocket(url, protocols) as unknown as DemoWebSocket;
    }
  }
  window.WebSocket = DemoWebSocket as unknown as typeof WebSocket;
}
