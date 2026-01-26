import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Auth', () => {
  test('invalid login shows error', async ({ page }) => {
    await page.goto('/login');
    await page.evaluate(() => localStorage.clear());
    await page.reload();

    await page.locator('input[name="username"]').fill('admin');
    await page.locator('input[name="password"]').fill('wrong-password');
    await page.getByRole('button', { name: 'Anmelden' }).click();

    await expect(page.locator('.error-msg')).toBeVisible();
  });

  test('login and logout redirects to login', async ({ page }) => {
    await login(page);
    await page.getByRole('button', { name: /Logout/i }).click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('input[name="username"]')).toBeVisible();

    await page.goto('/templates');
    await expect(page).toHaveURL(/\/login/);
  });
});
