import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Notifications', () => {
  test('shows error toast for invalid raw config', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    await page.getByRole('button', { name: 'System' }).click();

    const rawCard = page.locator('.card', { has: page.getByRole('heading', { name: /Roh-Konfiguration/i }) });
    const rawArea = rawCard.locator('textarea');
    await rawArea.fill('{');
    await rawCard.getByRole('button', { name: /Roh-Daten Speichern/i }).click();

    const errorToast = page.locator('.notification.error', { hasText: /JSON/i });
    await expect(errorToast).toBeVisible();
  });
});
