import { test, expect } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, HUB_URL, TEST_LOGIN_IP, clearLoginAttempts, ensureLoginAttemptsCleared, loginFast, resetUserAuthStateViaApi } from './utils';

test.describe('Auth', () => {
  test.beforeEach(() => {
    if (TEST_LOGIN_IP) {
      clearLoginAttempts(TEST_LOGIN_IP);
    }
  });

  test('invalid login shows error', async ({ request }) => {
    const res = await request.post(`${HUB_URL}/login`, {
      data: { username: 'auth-invalid-user', password: 'wrong-password' }
    });
    const usingExisting = process.env.ANANTA_E2E_USE_EXISTING === '1';
    if (usingExisting) {
      expect([401, 403, 429]).toContain(res.status());
    } else {
      expect([401, 403]).toContain(res.status());
    }
  });

  test('login and logout redirects to login', async ({ page, request }) => {
    test.setTimeout(120000);
    await resetUserAuthStateViaApi(ADMIN_USERNAME, ADMIN_PASSWORD);
    await ensureLoginAttemptsCleared(TEST_LOGIN_IP);
    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard|Ananta starten/i })).toBeVisible();
    await page.evaluate(() => {
      localStorage.removeItem('ananta.user.token');
      localStorage.removeItem('ananta.user.refresh_token');
    });

    await page.goto('/templates', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('input[name="username"]')).toBeVisible();
  });

  test('session persists after reload', async ({ page, request }) => {
    await resetUserAuthStateViaApi(ADMIN_USERNAME, ADMIN_PASSWORD);
    await ensureLoginAttemptsCleared(TEST_LOGIN_IP);
    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard|Ananta starten/i })).toBeVisible();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard|Ananta starten/i })).toBeVisible();
  });

  test('accessing protected route without login redirects to login', async ({ page }) => {
    await page.goto('/settings', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/login/);
  });
});
