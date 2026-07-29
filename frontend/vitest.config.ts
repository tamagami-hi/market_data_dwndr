import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./test/setup.ts"],
    include: ["**/*.test.{ts,tsx}"],
    clearMocks: true,
    restoreMocks: true,
    coverage: {
      provider: "v8",
      reporter: ["text", "json", "json-summary", "html", "lcov"],
      include: [
        "app/**/*.{ts,tsx}",
        "components/**/*.{ts,tsx}",
        "hooks/**/*.ts",
        "lib/api.ts",
        "lib/automationStatus.ts",
        "lib/branding.ts",
        "lib/downloader/**/*.ts",
        "lib/monitor/**/*.ts",
        "lib/numberFormat.ts",
        "lib/operatorEvents.ts",
        "lib/options/**/*.ts",
        "lib/stockBoard.ts",
        "lib/stockDepth.ts",
      ],
      exclude: ["**/*.test.{ts,tsx}", "lib/wsTypes.ts"],
      thresholds: {
        statements: 80,
        branches: 80,
        functions: 80,
        lines: 80,
      },
    },
  },
});
