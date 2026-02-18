import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    globals: true,
    include: ['src/**/*.spec.ts'],
    setupFiles: ['src/test-setup.ts'],
    environment: 'node',
    passWithNoTests: false
  }
});
