/// <reference types="vitest" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'node:path'

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    setupFiles: ['./src/tests/setup.ts'],
    // Tests run against the fixtures, never the network. Without this, client.ts
    // would try to fetch a real backend and every page test would render its
    // error state instead of the thing under test.
    env: { VITE_MOCK: '1' },
  },
})
