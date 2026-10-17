import { fileURLToPath } from 'node:url'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  optimizeDeps: { exclude: ['maplibre-gl'] },
  resolve: {
    alias: { '@fixtures': fileURLToPath(new URL('../fixtures', import.meta.url)) },
  },
  server: {
    fs: { allow: ['..'] },
    proxy: {
      '/api': { target: 'http://localhost:8000', rewrite: (p) => p.replace(/^\/api/, '') },
    },
  },
})
