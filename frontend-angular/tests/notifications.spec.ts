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
    
    const configGetPromise = page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'GET');
    await page.goto('/settings');
    await configGetPromise;

    const rawArea = page.locator('textarea');
    await rawArea.fill(JSON.stringify({ ...config, default_model: 'gpt-4o-mini' }, null, 2));
    
    const configPostPromise1 = page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'POST');
    await page.getByRole('button', { name: /Roh-Daten Speichern/i }).click();
    await configPostPromise1;

    const successToast = page.locator('.notification.success');
    await expect(successToast).toBeVisible();
    await expect(successToast).toBeHidden({ timeout: 7000 });

    await rawArea.fill('{');
    
    const configPostPromise2 = page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'POST');
    await page.getByRole('button', { name: /Roh-Daten Speichern/i }).click();
    await configPostPromise2;

    const errorToast = page.locator('.notification.error');
    await expect(errorToast).toBeVisible();
    await expect(errorToast).toBeHidden({ timeout: 7000 });
  });
});
