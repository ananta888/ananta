import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

const backendUrl = process.env.BACKEND_URL || 'http://localhost:8081'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    exclude: ['e2e/**', 'node_modules/**']
  },
  server: {
    proxy: {
      '/config': {
        target: backendUrl,
        changeOrigin: true
      },
      '/next-config': {
        target: backendUrl,
        changeOrigin: true
      },
      '/agent': {
        target: backendUrl,
        changeOrigin: true
      },
      '/stop': {
        target: backendUrl,
        changeOrigin: true
      },
      '/restart': {
        target: backendUrl,
        changeOrigin: true
      },
      '/export': {
        target: backendUrl,
        changeOrigin: true
      }
    }
  },
  base: '/ui/',
  build: {
    outDir: 'dist'
  }
})