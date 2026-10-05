import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The UI is static files. In development Vite proxies the action API to Django;
// in the Compose stack nginx does the same, so the session cookie stays same-origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.KPIGO_API ?? "http://localhost:8000", changeOrigin: false } },
  },
  build: { outDir: "dist", sourcemap: false, chunkSizeWarningLimit: 1200 },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
    css: false,
    globals: true,
  },
});
