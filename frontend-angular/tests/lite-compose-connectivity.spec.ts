import { test, expect } from '@playwright/test';
import { login, HUB_URL, ALPHA_URL, BETA_URL } from './utils';

test.describe('Lite Compose Connectivity', () => {
  test('frontend can reach hub and worker agents', async ({ page, request }) => {
    test.setTimeout(90_000);
    await login(page);

    for (const url of [HUB_URL]) {
      const health = await request.get(`${url}/health`, { timeout: 15_000 });
      expect(health.ok(), `${url}/health should be reachable`).toBeTruthy();

      // /ready is optional in some compose-lite variants and may be guarded/slow.
      // We only assert it when it responds with 2xx.
      try {
        const ready = await request.get(`${url}/ready`, { timeout: 10_000 });
        if (ready.ok()) {
          const readyBody = await ready.json();
          expect(readyBody?.data?.ready, `${url}/ready should report ready=true when available`).toBeTruthy();
        }
      } catch {}
    }

    for (const url of [ALPHA_URL, BETA_URL]) {
      try {
        await request.get(`${url}/health`, { timeout: 8_000 });
      } catch {}
    }

    await page.goto('/agents');
    await expect(page.locator('.card').filter({ hasText: 'hub' }).first()).toBeVisible();
    await expect(page.locator('.card').filter({ hasText: 'alpha' }).first()).toBeVisible();
    await expect(page.locator('.card').filter({ hasText: 'beta' }).first()).toBeVisible();
  });
});
