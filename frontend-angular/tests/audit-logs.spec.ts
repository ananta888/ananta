import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Audit Logs', () => {
  test('paginates and filters logs', async ({ page }) => {
    const page1 = [
      { timestamp: 1710000000, username: 'user-0', ip: '127.0.0.1', action: 'login', details: { ok: true } },
      { timestamp: 1710000001, username: 'user-1', ip: '127.0.0.2', action: 'update', details: { field: 'role' } }
    ];
    const page2 = [
      { timestamp: 1710000020, username: 'user-20', ip: '127.0.0.3', action: 'delete', details: { target: 'template' } }
    ];

    await page.route('**/audit-logs?*', async route => {
      const url = new URL(route.request().url());
      const offset = Number(url.searchParams.get('offset') || '0');
      const data = offset >= 20 ? page2 : page1;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify(data)
      });
    });

    await login(page);
    await page.goto('/audit-log');

    await expect(page.getByText('user-0')).toBeVisible();
    await expect(page.getByText('user-1')).toBeVisible();

    await page.getByLabel('Filter').fill('user-0');
    await expect(page.getByText('user-0')).toBeVisible();
    await expect(page.getByText('user-1')).toHaveCount(0);

    await page.getByLabel('Filter').fill('');
    await page.getByRole('button', { name: /Weiter/i }).click();
    await expect(page.getByText('Offset: 20')).toBeVisible();
    await expect(page.getByText('user-20')).toBeVisible();
  });
});
