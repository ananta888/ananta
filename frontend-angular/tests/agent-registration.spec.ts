import { test, expect } from '@playwright/test';
import { clearLoginAttempts, loginFast } from './utils';

test.describe('Agent Registration', () => {
  test.beforeEach(() => {
    clearLoginAttempts('127.0.0.1');
  });

  test('shows registered worker in dashboard', async ({ page, request }) => {
    test.setTimeout(120_000);
    let agentsRequested = false;
    await page.route('**/agents*', async route => {
      agentsRequested = true;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            name: 'worker-echo',
            status: 'online',
            role: 'worker',
            resources: { cpu_percent: 1, ram_bytes: 1048576 }
          }
        ])
      });
    });

    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });

    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    await expect.poll(() => agentsRequested, { timeout: 15_000 }).toBeTruthy();
  });
});
