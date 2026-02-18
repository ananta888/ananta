import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Admin Core Journey', () => {
  test('navigates core areas and keeps assistant visible', async ({ page }) => {
    await login(page);
    await page.goto('/dashboard');
    await expect(page.getByText(/System Dashboard/i)).toBeVisible();

    await page.goto('/teams');
    await expect(page.getByText(/Management/i)).toBeVisible();

    await page.goto('/templates');
    await expect(page.getByText(/Templates/i)).toBeVisible();

    await page.goto('/settings');
    await expect(page.getByText(/System-Einstellungen/i)).toBeVisible();

    await expect(page.locator('app-ai-assistant')).toBeVisible();
  });
});
