import type { NextConfig } from "next";

// The dashboard is exported as static files and served by the API, so there is no
// Node server on the Pi at run time. `next dev` proxies /api to a running API for development.
const development = process.env.NODE_ENV === "development";

// `next dev` writes an AGENTS.md of instructions for AI tools into the project. This project keeps
// its guidance for such tools in CLAUDE.md at the top, so the generator is off.
// `trailingSlash` makes the export write each page as a folder with an index.html (history/index.html),
// which the API's static files serve at /history/ without any rewrite rule.
const shared = { agentRules: false, trailingSlash: true } as const;

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
