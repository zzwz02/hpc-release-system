import { fileURLToPath } from "node:url";

import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig(({ mode }) => {
  const repoRoot = fileURLToPath(new URL("..", import.meta.url));
  const env = loadEnv(mode, repoRoot, "");
  return {
    plugins: [react()],
    define: {
      __GERRIT_SSH_BASE_URL__: JSON.stringify(env.GERRIT_SSH_BASE_URL || ""),
    },
    server: {
      fs: {
        // Frontend policy and integration defaults live in repo-level shared/.
        allow: [".."],
      },
      proxy: {
        // Forward all /api/* calls to the FastAPI backend.
        // The system http_proxy hijacks localhost — bypass it here.
        "/api": {
          target: process.env.API_TARGET || "http://127.0.0.1:8000",
          changeOrigin: false,
          // Do NOT go through the system proxy; connect directly to 127.0.0.1.
          configure: (proxy) => {
            // @ts-expect-error undocumented node option
            proxy.options.agent = null;
          },
        },
      },
    },
    build: {
      outDir: "../web_dist",
      emptyOutDir: true,
    },
  };
});
