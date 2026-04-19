import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/login': 'http://127.0.0.1:8000',
      '/signup': 'http://127.0.0.1:8000',
      '/chat': 'http://127.0.0.1:8000',
      '/diary': 'http://127.0.0.1:8000',
      '/materials': 'http://127.0.0.1:8000',
      '/cognify': 'http://127.0.0.1:8000',
      '/health': 'http://127.0.0.1:8000',
      '/quiz': 'http://127.0.0.1:8000',
      '/index-status': 'http://127.0.0.1:8000',
      '/ws': {
        target: 'ws://127.0.0.1:8000',
        ws: true,
      },
    },
  },
})
