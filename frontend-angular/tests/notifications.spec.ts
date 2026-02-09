import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Notifications', () => {
  test('shows success/error toasts and auto-dismisses them', async ({ page }) => {
    let config: any = { default_provider: 'openai', default_model: 'gpt-4o' };

    await page.route('http://localhost:5000/config', async route => {
      if (route.request().method() === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(config)
        });
        return;
      }
      if (route.request().method() === 'POST') {
        config = JSON.parse(route.request().postData() || '{}');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(config)
        });
        return;
      }
      await route.continue();
    });

    await login(page);
    await page.goto('/settings');

    const rawArea = page.locator('textarea');
    await rawArea.fill(JSON.stringify({ ...config, default_model: 'gpt-4o-mini' }, null, 2));
    await page.getByRole('button', { name: /Roh-Daten Speichern/i }).click();

    const successToast = page.locator('.notification.success');
    await expect(successToast).toBeVisible();
    await expect(successToast).toBeHidden({ timeout: 7000 });

    await rawArea.fill('{');
    await page.getByRole('button', { name: /Roh-Daten Speichern/i }).click();

    const errorToast = page.locator('.notification.error');
    await expect(errorToast).toBeVisible();
    await expect(errorToast).toBeHidden({ timeout: 7000 });
  });
});
