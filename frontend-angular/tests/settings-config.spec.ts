import { test, expect } from '@playwright/test';
import { HUB_URL, login } from './utils';

test.describe('Settings Config', () => {
  test('loads, saves, and validates raw config', async ({ page }) => {
    let config: any = {
      default_provider: 'openai',
      default_model: 'gpt-4o',
      http_timeout: 20
    };

    await page.route(`${HUB_URL}/config`, async route => {
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
    
    const configGetPromise1 = page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'GET');
    await page.goto('/settings');
    await configGetPromise1;

    const rawArea = page.locator('textarea');
    await expect(rawArea).toHaveValue(/"default_model":\s*"gpt-4o"/);

    const updated = { ...config, http_timeout: 42, command_timeout: 30 };
    await rawArea.fill(JSON.stringify(updated, null, 2));
    
    const configPostPromise1 = page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'POST');
    await page.getByRole('button', { name: /Roh-Daten Speichern/i }).click();
    await configPostPromise1;
    
    await expect(page.locator('.notification.success')).toBeVisible();

    const configGetPromise2 = page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'GET');
    await page.reload();
    await configGetPromise2;
    
    await expect(rawArea).toHaveValue(/"http_timeout":\s*42/);
    await expect(rawArea).toHaveValue(/"command_timeout":\s*30/);

    await rawArea.fill('{');
    
    const configPostPromise2 = page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'POST');
    await page.getByRole('button', { name: /Roh-Daten Speichern/i }).click();
    await configPostPromise2;
    
    await expect(page.locator('.notification.error')).toBeVisible();
  });
});
