import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    include: ['src/**/*.spec.ts'],
    setupFiles: ['src/test-setup.ts'],
    environment: 'jsdom',
    passWithNoTests: false
  }
});
