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
    await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();

    await page.goto('/settings');
    await expect(page.getByText(/System-Einstellungen/i)).toBeVisible();

    const assistant = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    await expect(assistant).toBeVisible();
    const state = await assistant.getAttribute('data-state');
    if (state === 'minimized') {
      await expect(page.locator('[data-testid="assistant-dock-header"], .ai-assistant-container .header').first()).toBeVisible();
    } else {
      await expect(page.locator('[data-testid="assistant-dock-input"], input[placeholder=\"Ask me anything...\"]').first()).toBeVisible();
    }
  });
});
