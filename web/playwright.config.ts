/**
 * Playwright configuration for Phase-3 e2e key-flow tests.
 *
 * Target: Vite dev server on 127.0.0.1:5176 (already running) or auto-started.
 * Backend: FastAPI uvicorn on 127.0.0.1:8000 (must be running).
 *
 * http_proxy trap: the system sets http_proxy → we bypass with NO_PROXY env
 * and Playwright's own proxy:null option for localhost traffic.
 */
import { defineConfig, devices } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 0,
  workers: 1, // serial — shared session cookies
  reporter: [["list"], ["html", { outputFolder: "playwright-report", open: "never" }]],

  use: {
    baseURL: "http://127.0.0.1:5176",
    // Bypass system http_proxy for localhost connections.
    proxy: { server: "http://127.0.0.1:3576", bypass: "127.0.0.1,localhost" },
    headless: true,
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },

  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],

  // Do NOT auto-start the dev server — it must already be running.
  // Run `npm run dev` (port 5176) and ensure uvicorn is on :8000 first.
  // webServer blocks are omitted so the suite doesn't re-launch vite.
});
