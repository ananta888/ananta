import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Notifications', () => {
  test('does not submit invalid raw config', async ({ page }) => {
    await login(page);
    await page.goto('/settings?section=system');
    await expect(page.getByRole('heading', { name: /System Parameter/i })).toBeVisible();

    const rawCard = page.locator('.card', { has: page.getByRole('heading', { name: /Roh-Konfiguration/i }) });
    const rawArea = rawCard.locator('textarea');
    let configPostSeen = false;
    await page.route('**/config', async (route) => {
      if (route.request().method() === 'POST') configPostSeen = true;
      await route.continue();
    });
    await rawArea.fill('{');
    await rawCard.getByRole('button', { name: /Roh-Daten Speichern/i }).click();
    await page.waitForTimeout(500);
    expect(configPostSeen).toBeFalsy();
  });
});
