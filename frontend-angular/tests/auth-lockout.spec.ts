import { test, expect } from '@playwright/test';
import { ADMIN_USERNAME, ADMIN_PASSWORD, setUserLockout, clearUserLockout } from './utils';

test.describe('Auth Lockout', () => {
  test('locked account shows error message', async ({ page }) => {
    setUserLockout();

    try {
      await page.goto('/login');
      await page.locator('input[name="username"]').fill(ADMIN_USERNAME);
      await page.locator('input[name="password"]').fill(ADMIN_PASSWORD);

      const loginPromise = page.waitForResponse(res => res.url().includes('/login'));
      await page.getByRole('button', { name: 'Anmelden' }).click();
      await loginPromise;

      await expect(page.locator('.error-msg')).toContainText(/Account is locked/i);
    } finally {
      clearUserLockout();
    }
  });
});
