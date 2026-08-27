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

// `next dev` compiles in the browser and needs eval for it. Production does not, and this is
// the only difference between the two policies.
const DEV = process.env.NODE_ENV !== "production";

// Looser than the API's `default-src 'none'`, because these are pages a person looks at
// rather than JSON. Two entries are compromises and both are named as such:
//
// `script-src 'unsafe-inline'` — Next inlines its bootstrap and flight data into the
// document. Removing it means a per-request nonce, which has to be generated in proxy.ts and
// threaded through `createMiddleware(routing)` from next-intl. That is the upgrade path if
// this page ever renders markup it did not author; today it renders publisher headlines as
// React text, which is escaped.
//
// `style-src 'unsafe-inline'` — Tailwind v4 and Next both emit inline style attributes.
// There is no nonce path for those at all.
const CSP = [
  "default-src 'self'",
  `script-src 'self' 'unsafe-inline'${DEV ? " 'unsafe-eval'" : ""}`,
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data:",
  "font-src 'self'",
  "connect-src 'self'",
  "object-src 'none'",
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join("; ");

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

  async headers() {
    return [
      {
        // Everything except `/api`, which is rewritten to FastAPI and ships its own, far
        // stricter policy from middleware. Two policies on one response would mean the
        // browser enforcing the intersection, which is harder to reason about than either.
        source: "/((?!api/).*)",
        headers: [
          { key: "Content-Security-Policy", value: CSP },
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "no-referrer" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=(), interest-cohort=()",
          },
        ],
      },
    ];
  },
};

export default createNextIntlPlugin()(nextConfig);
