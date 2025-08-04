import { defineConfig } from 'vite';
import vue from '@vitejs/plugin-vue';

export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/config': 'http://localhost:8081',
      '/agent': 'http://localhost:8081',
      '/stop': 'http://localhost:8081',
      '/restart': 'http://localhost:8081',
      '/export': 'http://localhost:8081'
    }
  },
  build: {
    outDir: 'dist'
  }
});
