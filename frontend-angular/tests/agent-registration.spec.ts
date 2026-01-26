import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Agent Registration', () => {
  test('shows registered worker in dashboard', async ({ page }) => {
    await page.route('**/agents', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          'worker-echo': {
            status: 'online',
            role: 'worker',
            resources: { cpu_percent: 1, ram_bytes: 1048576 }
          }
        })
      });
    });

    await login(page);
    await page.goto('/dashboard');

    await expect(page.getByText('Agenten Status')).toBeVisible();
    await expect(page.getByText('worker-echo')).toBeVisible();
  });
});
