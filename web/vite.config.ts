import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Dev: `uvicorn --factory api.main:from_env` on :8000, `npm run dev` here; the API is proxied.
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
});
