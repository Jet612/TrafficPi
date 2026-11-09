import type { NextConfig } from "next";

// The dashboard is exported as static files and served by the API, so there is no
// Node server on the Pi at run time. `next dev` proxies /api to a running API for development.
const development = process.env.NODE_ENV === "development";

// `next dev` would write an AGENTS.md of instructions for AI tools into the project, a file this
// repository does not keep, so the generator is off.
// `trailingSlash` makes the export write each page as a folder with an index.html (history/index.html),
// which the API's static files serve at /history/ without any rewrite rule.
// The static demo (src/demo, scripts/build_static_demo.sh) is switched on by NEXT_PUBLIC_TRAFFICPI_DEMO=1.
// Both values are always defined, so that the build writes them into the code as plain strings and
// an ordinary build can leave the demo's branches, and the code they load, out.
const shared = {
  agentRules: false,
  trailingSlash: true,
  env: {
    NEXT_PUBLIC_TRAFFICPI_DEMO: process.env.NEXT_PUBLIC_TRAFFICPI_DEMO ?? "",
    NEXT_PUBLIC_TRAFFICPI_DEMO_AT: process.env.NEXT_PUBLIC_TRAFFICPI_DEMO_AT ?? "",
  },
} as const;

const config: NextConfig = development
  ? {
      ...shared,
      async rewrites() {
        const api = process.env.TRAFFICPI_API ?? "http://127.0.0.1:8000";
        return [{ source: "/api/:path*", destination: `${api}/api/:path*` }];
      },
    }
  : { ...shared, output: "export", images: { unoptimized: true } };

export default config;
