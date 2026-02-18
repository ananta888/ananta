import { test, expect } from '@playwright/test';
import { login, HUB_URL, ALPHA_URL, BETA_URL } from './utils';

test.describe('Lite Compose Connectivity', () => {
  test('frontend can reach hub and worker agents', async ({ page }) => {
    await login(page);

    for (const url of [HUB_URL, ALPHA_URL, BETA_URL]) {
      const health = await page.request.get(`${url}/health`);
      expect(health.ok(), `${url}/health should be reachable`).toBeTruthy();

      const ready = await page.request.get(`${url}/ready`);
      expect(ready.ok(), `${url}/ready should be reachable`).toBeTruthy();
      const readyBody = await ready.json();
      expect(readyBody?.data?.ready, `${url}/ready should report ready=true`).toBeTruthy();
    }

    await page.goto('/agents');
    await expect(page.locator('.card').filter({ hasText: 'hub' }).first()).toBeVisible();
    await expect(page.locator('.card').filter({ hasText: 'alpha' }).first()).toBeVisible();
    await expect(page.locator('.card').filter({ hasText: 'beta' }).first()).toBeVisible();
  });
});
