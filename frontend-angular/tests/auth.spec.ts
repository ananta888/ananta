import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Auth', () => {
  test('invalid login shows error', async ({ page }) => {
    await page.goto('/login');
    await page.evaluate(() => localStorage.clear());
    await page.reload();

    await page.locator('input[name="username"]').fill('admin');
    await page.locator('input[name="password"]').fill('wrong-password');
    
    const loginPromise = page.waitForResponse(res => res.url().includes('/login'));
    await page.getByRole('button', { name: 'Anmelden' }).click();
    await loginPromise;

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

  test('session persists after reload', async ({ page }) => {
    await login(page);
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    await page.reload();
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
  });

  test('accessing protected route without login redirects to login', async ({ page }) => {
    await page.goto('/settings');
    await expect(page).toHaveURL(/\/login/);
  });
});
