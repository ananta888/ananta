import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Admin Core Journey', () => {
  async function ensureAssistantAvailable(page: any) {
    let container = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    if (await container.count() === 0) {
      const opener = page.getByText(/AI Assistant/i).first();
      if (await opener.count()) {
        await opener.click();
      }
      container = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    }
    if (await container.count() === 0) {
      return;
    }
    await expect(container).toBeVisible({ timeout: 15000 });
    const state = await container.getAttribute('data-state');
    if (state === 'minimized') {
      await page.locator('[data-testid="assistant-dock-header"], .ai-assistant-container .header, .ai-assistant-container button').first().click();
    }
    await expect(
      page.locator('[data-testid="assistant-dock-input"], input[placeholder="Ask me anything..."], input[placeholder*="Frage mich"]').first()
    ).toBeVisible({ timeout: 15000 });
  }

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

    await ensureAssistantAvailable(page);
  });
});
