import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    // Keep the frontend URL stable for Firebase/CORS and local testing.
    port: 5174,
    strictPort: true,
    // Forward local API calls to FastAPI; the browser sees same-origin /api requests.
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: true,
      },
    },
  },
});
