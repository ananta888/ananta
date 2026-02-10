import { test, expect } from '@playwright/test';
import { ADMIN_USERNAME, ADMIN_PASSWORD, seedLoginAttempts, clearLoginAttempts } from './utils';

test.describe('Auth Rate Limit', () => {
  test('too many attempts returns rate limit error', async ({ page }) => {
    const ip = '127.0.0.1';
    seedLoginAttempts(ip, 10);

    try {
      await page.goto('/login');
      await page.locator('input[name="username"]').fill(ADMIN_USERNAME);
      await page.locator('input[name="password"]').fill(ADMIN_PASSWORD);

      const loginPromise = page.waitForResponse(res => res.url().includes('/login'));
      await page.getByRole('button', { name: 'Anmelden' }).click();
      await loginPromise;

      await expect(page.locator('.error-msg')).toContainText(/Too many login attempts/i);
    } finally {
      clearLoginAttempts(ip);
    }
  });
});
