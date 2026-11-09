/** Where the published source is. */
const SOURCE_URL = "https://github.com/Jet612/TrafficPi";

/** The line at the top of every page of the demo: what this is, and where the code is. */
export function DemoBanner() {
  return (
    <div role="note" className="border-b border-edge bg-accent px-4 py-2 text-center text-sm text-accent-ink">
      Demo: a recording of TrafficPi on a made-up street, played back in your browser. Changes last until you reload.{" "}
      <a href={SOURCE_URL} className="font-medium underline underline-offset-2">
        Source on GitHub
      </a>
    </div>
  );
}
