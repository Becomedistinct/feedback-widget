import { defineConfig } from "vite";

export default defineConfig({
  build: {
    lib: {
      entry: "src/main.ts",
      name: "FeedbackWidget",
      formats: ["iife"],
      fileName: () => "feedback-widget.js",
    },
  },
  server: {
    port: 5175,
    proxy: {
      "/api": {
        target: "http://localhost:8002",
        changeOrigin: true,
      },
    },
  },
});
