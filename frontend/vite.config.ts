import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

const api = process.env.HAVEN_API ?? "http://localhost:8000";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "autoUpdate",
      includeAssets: ["icon.svg", "apple-touch-icon-180x180.png"],
      manifest: {
        id: "/",
        name: "Haven",
        short_name: "Haven",
        description: "Official hazard data, turned into what to do next.",
        start_url: "/",
        scope: "/",
        display: "standalone",
        background_color: "#ffffff",
        theme_color: "#0f172a",
        icons: [
          { src: "pwa-64x64.png", sizes: "64x64", type: "image/png" },
          { src: "pwa-192x192.png", sizes: "192x192", type: "image/png" },
          { src: "pwa-512x512.png", sizes: "512x512", type: "image/png" },
          { src: "maskable-icon-512x512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
          { src: "icon.svg", sizes: "any", type: "image/svg+xml" },
        ],
        shortcuts: [{ name: "Crisis mode", short_name: "Crisis", url: "/crisis", icons: [{ src: "pwa-192x192.png", sizes: "192x192" }] }],
      },
      workbox: {
        // Precache the whole built shell so the app opens offline. API responses are never
        // cached here (the active route is kept in localStorage by the app for low connectivity).
        globPatterns: ["**/*.{js,css,html,svg,png,woff2}"],
        navigateFallback: "/index.html",
        navigateFallbackDenylist: [/^\/api/],
        runtimeCaching: [
          {
            // Basemap style, tiles, glyphs and sprites: keep recently viewed areas available offline.
            urlPattern: ({ url }) => url.origin === "https://tiles.openfreemap.org",
            handler: "StaleWhileRevalidate",
            options: {
              cacheName: "basemap",
              expiration: { maxEntries: 2000, maxAgeSeconds: 30 * 24 * 60 * 60 },
              cacheableResponse: { statuses: [0, 200] },
            },
          },
        ],
      },
    }),
  ],
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
