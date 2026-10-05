/// <reference types="vitest/config" />
import { readFileSync } from 'node:fs';
import { defineConfig } from 'vite';
import { svelte } from '@sveltejs/vite-plugin-svelte';

const pkg = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8')) as { version: string };

// Served by FastAPI at / (and by Pantheon under /hermes/); in dev, Vite serves
// the UI and forwards /api (including the WebSocket) and /health to the API,
// which is on :8002 unless HERMES_PORT says otherwise (`make run-dev PORT=…`).
const apiPort = process.env.HERMES_PORT ?? '8002';
const api = `http://localhost:${apiPort}`;

export default defineConfig({
  base: './',
  plugins: [svelte()],
  define: { __APP_VERSION__: JSON.stringify(pkg.version) },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: api, ws: true },
      '/health': api,
    },
  },
  build: { outDir: 'dist', emptyOutDir: true },
  test: { include: ['src/**/*.test.ts'], environment: 'node' },
});
