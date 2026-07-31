import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
    base: "./",
    plugins: [react()],
    build: {
        outDir: "../oddsfox_graph/static/explorer",
        emptyOutDir: true,
        sourcemap: false,
    },
    server: {
        proxy: {
            "/api": "http://127.0.0.1:8765",
        },
    },
});
