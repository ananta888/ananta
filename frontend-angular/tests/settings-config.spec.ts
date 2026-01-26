import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Settings Config', () => {
  test('loads, saves, and validates raw config', async ({ page }) => {
    let config: any = {
      default_provider: 'openai',
      default_model: 'gpt-4o',
      http_timeout: 20
    };

    await page.route('**/config', async route => {
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
    await expect(rawArea).toHaveValue(/"default_model":\s*"gpt-4o"/);

    const updated = { ...config, http_timeout: 42, command_timeout: 30 };
    await rawArea.fill(JSON.stringify(updated, null, 2));
    await page.getByRole('button', { name: /Roh-Daten Speichern/i }).click();
    await expect(page.locator('.notification.success')).toBeVisible();

    await page.reload();
    await expect(rawArea).toHaveValue(/"http_timeout":\s*42/);
    await expect(rawArea).toHaveValue(/"command_timeout":\s*30/);

    await rawArea.fill('{');
    await page.getByRole('button', { name: /Roh-Daten Speichern/i }).click();
    await expect(page.locator('.notification.error')).toBeVisible();
  });
});
