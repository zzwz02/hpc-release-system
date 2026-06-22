import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      // Forward all /api/* calls to the FastAPI backend.
      // The system http_proxy hijacks localhost — bypass it here.
      "/api": {
        target: "http://127.0.0.1:8000",
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
});
