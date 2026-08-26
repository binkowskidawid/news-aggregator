import { defineConfig } from "@playwright/test";

/**
 * The API is not started here. These tests read the corpus through the same rewrite the
 * browser uses, so something has to be serving it: `make api` locally, a step in the
 * workflow in CI. Starting it from the front end's own config would hide which layer failed.
 */
export default defineConfig({
  testDir: "./e2e",
  forbidOnly: Boolean(process.env.CI),
  reporter: process.env.CI ? "github" : "list",
  use: { baseURL: "http://127.0.0.1:3000" },
  webServer: {
    // The production build, not `next dev`: the thing a reader meets is what should be
    // asserted, and a dev-only rendering difference would pass here and fail in the world.
    command: "pnpm build && pnpm start",
    url: "http://127.0.0.1:3000/pl",
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
