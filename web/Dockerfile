# pnpm is installed rather than enabled through corepack: corepack's future in the Node
# images is not something this build should depend on, and the version is pinned to the one
# that produced pnpm-lock.yaml.
FROM node:25-slim AS deps
WORKDIR /app
RUN npm install --global pnpm@11.21.0
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

FROM node:25-slim AS builder
WORKDIR /app
RUN npm install --global pnpm@11.21.0
COPY --from=deps /app/node_modules ./node_modules
COPY . .

# Frozen into the build, not read at run time: `next build` resolves rewrites into
# .next/routes-manifest.json. The default is the compose service name, because that is the
# only topology this image is built for; a different one rebuilds with --build-arg.
ARG API_URL=http://api:8000
ENV API_URL=${API_URL}

# Asked for here rather than in next.config.ts, because `next start` — which the browser
# tests use — refuses to run against a standalone build.
ENV NEXT_STANDALONE=1
RUN pnpm build

FROM node:25-slim
# Unprivileged, like the API container. This one renders text fetched from third-party sites.
RUN useradd --create-home --uid 10001 app
WORKDIR /app

# `output: "standalone"` writes a server and the node_modules it traced, but deliberately
# leaves the static assets out of it — they are copied separately or they 404.
COPY --from=builder --chown=app:app /app/.next/standalone ./
COPY --from=builder --chown=app:app /app/.next/static ./.next/static

USER app
# server.js reads both; without HOSTNAME it binds to localhost inside the container and
# nothing outside it can connect.
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
EXPOSE 3000
CMD ["node", "server.js"]
