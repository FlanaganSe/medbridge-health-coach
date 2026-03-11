import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      "/v1": "http://localhost:8000",
      "/webhooks": "http://localhost:8000",
    },
  },
});
