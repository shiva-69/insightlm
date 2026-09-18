import type { NextConfig } from "next";

const API_ORIGIN = process.env.API_ORIGIN ?? "http://localhost:8000";

const config: NextConfig = {
  reactStrictMode: true,
  // Next writes AGENTS.md / CLAUDE.md into the project on build. This repo already
  // documents itself in docs/ and docs/adr/; a second, generated set would drift.
  agentRules: false,
  // Traced standalone server for the container image.
  output: "standalone",
  // Proxy the API through Next rather than calling it cross-origin. Same-origin
  // requests mean no CORS configuration, no preflight latency, and the bearer token
  // never has to be attached to a third-party origin.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_ORIGIN}/:path*` }];
  },
};

export default config;
