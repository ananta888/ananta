import { test, expect } from '@playwright/test';
import { HUB_URL, login } from './utils';

test.describe('Auth', () => {
  test('invalid login shows error', async ({ request }) => {
    const res = await request.post(`${HUB_URL}/login`, {
      data: { username: 'admin', password: 'wrong-password' }
    });
    expect([401, 403]).toContain(res.status());
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
