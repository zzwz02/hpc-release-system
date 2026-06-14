import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { App } from "./App";
import { AuthProvider } from "./api/AuthContext";

/**
 * QueryClient with R2 defaults (phase3_brief.md §1):
 *  - staleTime: Infinity — data never goes stale automatically
 *  - refetchInterval: false — no background polling
 *  - refetchOnWindowFocus: false — no refetch on tab focus
 *  - refetchOnReconnect: false — no refetch on network reconnect
 *  - refetchOnMount: false — data moved only via explicit refetch/invalidate
 *
 * Exception: QA AI job poll (1 s while running) is the ONLY allowed interval.
 */
const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: Infinity,
      refetchInterval: false,
      refetchOnWindowFocus: false,
      refetchOnReconnect: false,
      refetchOnMount: false,
      retry: 1,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <App />
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
