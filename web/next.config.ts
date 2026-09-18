import type { NextConfig } from "next";

/* The API proxy lives in `app/api/[...path]/route.ts`, not in `rewrites()`.
   Rewrites are evaluated at build time and baked into the route manifest, so an
   env-var destination captures whatever was set during `docker build` — which for a
   container image is nothing. See the note in that file. */

const config: NextConfig = {
  reactStrictMode: true,
  // Next writes AGENTS.md / CLAUDE.md into the project on build. This repo already
  // documents itself in docs/ and docs/adr/; a second, generated set would drift.
  agentRules: false,
  // Traced standalone server for the container image.
  output: "standalone",
};

export default config;
