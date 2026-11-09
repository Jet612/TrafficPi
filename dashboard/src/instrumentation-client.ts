// Runs in the browser before the dashboard starts. Only the static demo uses it: there it puts the
// recording in place of the Pi (src/demo/install.ts). The test is written out here, not imported,
// so that an ordinary build sees a branch that never runs and leaves it out with the module it
// loads; `require` (not `import`) keeps the loading inside the branch and still finishes before the
// dashboard's first request.
if (process.env.NEXT_PUBLIC_TRAFFICPI_DEMO === "1") {
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { installDemo } = require("@/demo/install") as typeof import("@/demo/install");
  installDemo(Number(process.env.NEXT_PUBLIC_TRAFFICPI_DEMO_AT) || null);
}
