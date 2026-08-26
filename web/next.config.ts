import createNextIntlPlugin from "next-intl/plugin";
import type { NextConfig } from "next";

// The browser talks to one origin. `/api/*` is proxied to FastAPI rather than called
// cross-origin, which is what keeps the session cookie's SameSite=Lax working: it is the
// only defence against CSRF the API has, and reaching for CORS would remove it in order to
// solve a problem this rewrite does not create.
//
// Read at **build** time, unlike everywhere else. `next build` resolves `rewrites()` into
// .next/routes-manifest.json, so this value is frozen into the image and changing the
// environment later does not move it. Server Components read `process.env.API_URL` at
// runtime (see lib/api.ts), which is why the compose service sets it in both places.
const API_URL = process.env.API_URL ?? "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  // A self-contained server plus only the node_modules it traced, which is what the image
  // copies. `public/` and `.next/static` are deliberately left out of it by Next and are
  // copied separately — see web/Dockerfile.
  //
  // Only for the image build, because `next start` refuses to run against a standalone
  // build and says so. The browser tests drive `next start`, and the application build is
  // the same either way — what changes is the server wrapper around it.
  output: process.env.NEXT_STANDALONE === "1" ? "standalone" : undefined,

  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default createNextIntlPlugin()(nextConfig);
