import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

const dashboardProxy = {
  "/dashboard": {
    target: "http://127.0.0.1:3000",
    changeOrigin: true,
    ws: true,
  },
  "/_next": {
    target: "http://127.0.0.1:3000",
    changeOrigin: true,
    ws: true,
  },
  "/api": {
    target: "http://127.0.0.1:3000",
    changeOrigin: true,
  },
};

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: dashboardProxy,
  },
  preview: {
    proxy: dashboardProxy,
  },
});
