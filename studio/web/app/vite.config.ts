import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// The SPA is served by gstudio-web from web/app/dist (same origin as /api).
// In dev, proxy /api to a local server: GS_API=http://127.0.0.1:8080 npm run dev
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.GS_API || "http://127.0.0.1:8080", changeOrigin: false } },
  },
  build: {
    target: "es2020",
    chunkSizeWarningLimit: 1800,
    rollupOptions: {
      output: {
        manualChunks: {
          three: ["three", "@react-three/fiber", "@react-three/drei"],
          react: ["react", "react-dom", "react-router-dom", "zustand"],
          md: ["react-markdown", "remark-gfm"],
        },
      },
    },
  },
  test: { environment: "node", include: ["src/**/*.test.ts", "src/**/*.test.tsx"] },
} as any);
