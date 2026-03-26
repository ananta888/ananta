import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, ALPHA_URL, BETA_URL, HUB_URL, ensureLoginAttemptsCleared, clearLoginAttempts } from './utils';

async function loginFast(page: Page, request: APIRequestContext) {
  const response = await request.post(`${HUB_URL}/login`, {
    data: {
      username: ADMIN_USERNAME,
      password: ADMIN_PASSWORD,
    },
  });
  expect(response.ok()).toBeTruthy();
  const payload = await response.json();
  const accessToken = payload?.data?.access_token;
  const refreshToken = payload?.data?.refresh_token;
  expect(accessToken).toBeTruthy();

  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.evaluate(
    ({ hubUrl, alphaUrl, betaUrl, accessToken, refreshToken }) => {
      localStorage.clear();
      localStorage.setItem(
        'ananta.agents.v1',
        JSON.stringify([
          { name: 'hub', url: hubUrl, token: 'generate_a_random_token_for_hub', role: 'hub' },
          { name: 'alpha', url: alphaUrl, token: 'generate_a_random_token_for_alpha', role: 'worker' },
          { name: 'beta', url: betaUrl, token: 'generate_a_random_token_for_beta', role: 'worker' },
        ])
      );
      localStorage.setItem('ananta.user.token', accessToken);
      if (refreshToken) {
        localStorage.setItem('ananta.user.refresh_token', refreshToken);
      }
    },
    { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL, accessToken, refreshToken }
  );
}

test.describe('Auth', () => {
  test.beforeEach(() => {
    clearLoginAttempts('127.0.0.1');
  });

  test('invalid login shows error', async ({ request }) => {
    const res = await request.post(`${HUB_URL}/login`, {
      data: { username: 'admin', password: 'wrong-password' }
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
    await ensureLoginAttemptsCleared('127.0.0.1');
    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    await page.getByRole('button', { name: /Logout|Abmelden/i }).click();
    await expect(page).toHaveURL(/\/login/);
    await expect(page.locator('input[name="username"]')).toBeVisible();

    await page.goto('/templates', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/login/);
  });

  test('session persists after reload', async ({ page, request }) => {
    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
  });

  test('accessing protected route without login redirects to login', async ({ page }) => {
    await page.goto('/settings', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/login/);
  });
});
