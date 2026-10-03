import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const api = process.env.HAVEN_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true,
    port: 5173,
    proxy: { "/api": { target: api, changeOrigin: true } },
  },
  build: {
    rollupOptions: { output: { manualChunks: { maplibre: ["maplibre-gl"], react: ["react", "react-dom", "react-router-dom"] } } },
    chunkSizeWarningLimit: 900,
  },
  test: { environment: "node" },
});
