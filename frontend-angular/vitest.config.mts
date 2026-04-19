import { defineConfig } from 'vite';

export default defineConfig({
  test: {
    globals: true,
    include: ['src/**/*.spec.ts'],
    setupFiles: ['src/test-setup.ts'],
    environment: 'jsdom',
    passWithNoTests: false,
  },
});
