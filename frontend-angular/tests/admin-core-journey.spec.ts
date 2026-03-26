import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, ALPHA_URL, BETA_URL, HUB_URL } from './utils';

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

test.describe('Admin Core Journey', () => {
  test('navigates core areas', async ({ page, request }) => {
    test.setTimeout(120_000);
    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText(/System Dashboard/i)).toBeVisible();

    await page.goto('/teams', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /Teams werden ueber Blueprints erstellt/i })).toBeVisible();

    await page.goto('/templates', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();

    await page.goto('/settings', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText(/System-Einstellungen/i)).toBeVisible();
  });
});
