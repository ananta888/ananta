import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Auth Flow', () => {
  test('login succeeds with default admin', async ({ page }) => {
    await login(page);
  });

  test('login shows error on invalid credentials', async ({ page }) => {
    await page.goto('/login');
    await page.evaluate(() => localStorage.clear());
    await page.reload();
    await page.locator('input[name="username"]').fill('admin');
    await page.locator('input[name="password"]').fill('wrong');
    await page.getByRole('button', { name: 'Anmelden' }).click();

    await expect(page.locator('.error-msg')).toContainText(/Login fehlgeschlagen|Invalid/i);
  });
});
