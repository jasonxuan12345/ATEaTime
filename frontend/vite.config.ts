import react from "@vitejs/plugin-react";
import { loadEnv, searchForWorkspaceRoot } from "vite";
import { defineConfig } from "vitest/config";

const apiTarget = loadEnv("development", ".", "DEEPYIELD_").DEEPYIELD_API_URL || "http://127.0.0.1:8012";
const localDirectory = (relative: string) => decodeURIComponent(new URL(relative, import.meta.url).pathname).replace(/^\/([A-Za-z]:)/, '$1');

export default defineConfig({
  root: "workstation",
  plugins: [react()],
  build: {
    outDir: "../dist",
    emptyOutDir: true,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes("node_modules/echarts") || id.includes("node_modules/zrender")) {
            return "echarts";
          }
        },
      },
    },
  },
  server: {
    fs: { allow: [searchForWorkspaceRoot(localDirectory('./')), localDirectory('../config/i18n')] },
    host: "127.0.0.1",
    port: 5173,
    headers: { "X-DeepYield-Api-Target": apiTarget },
    proxy: {
      "/api": {
        target: apiTarget,
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: "src/test/setup.ts",
  },
});
