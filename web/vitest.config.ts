import { fileURLToPath } from "node:url";

import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { loadEnv } from "vite";

export default defineConfig(({ mode }) => {
  const repoRoot = fileURLToPath(new URL("..", import.meta.url));
  const env = loadEnv(mode, repoRoot, "");
  return {
    plugins: [react()],
    define: {
      __GERRIT_SSH_BASE_URL__: JSON.stringify(env.GERRIT_SSH_BASE_URL || ""),
    },
    test: {
      environment: "jsdom",
      globals: true,
      setupFiles: ["./src/test-setup.ts"],
      // Exclude Playwright e2e specs — those run via `npm run test:e2e`, not vitest
      exclude: ["**/node_modules/**", "**/e2e/**", "**/playwright-report/**"],
      // Ensure localhost calls are never routed through the system proxy
      env: {
        NO_PROXY: "localhost,127.0.0.1",
        no_proxy: "localhost,127.0.0.1",
      },
    },
  };
});
