import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: '/chat/',
  server: {
    port: 3001,
    proxy: {
      '/ws': { target: 'ws://localhost:8010', ws: true },
      '/api': { target: 'http://localhost:8010', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: true },
})
