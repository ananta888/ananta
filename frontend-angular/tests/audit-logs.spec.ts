import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Audit Logs', () => {
  test('paginates and filters logs', async ({ page }) => {
    const page1 = Array.from({ length: 20 }, (_, i) => ({
      timestamp: 1710000000 + i,
      username: `user-${i}`,
      ip: `127.0.0.${i + 1}`,
      action: i === 0 ? 'login' : 'update',
      details: i === 0 ? { ok: true } : { field: 'role' }
    }));
    const page2 = [
      { timestamp: 1710000020, username: 'user-20', ip: '127.0.0.3', action: 'delete', details: { target: 'template' } }
    ];

    await page.route('http://localhost:5000/audit-logs?*', async route => {
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
    
    // Wait for initial audit logs load
    const logsPromise = page.waitForResponse(res => res.url().includes('/audit-logs'));
    await page.goto('/audit-log');
    await logsPromise;

    await expect(page.getByText('user-0', { exact: true })).toBeVisible();
    await expect(page.getByText('user-1', { exact: true })).toBeVisible();

    await page.getByLabel('Filter').fill('user-0');
    await expect(page.getByText('user-0', { exact: true })).toBeVisible();
    await expect(page.getByText('user-1', { exact: true })).toHaveCount(0);

    await page.getByLabel('Filter').fill('');
    
    // Wait for pagination request
    const paginationPromise = page.waitForResponse(res => res.url().includes('/audit-logs') && res.url().includes('offset=20'));
    await page.getByRole('button', { name: /Weiter/i }).click();
    await paginationPromise;
    
    await expect(page.getByText('Offset: 20')).toBeVisible();
    await expect(page.getByText('user-20')).toBeVisible();
  });
});
