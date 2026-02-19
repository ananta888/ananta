import { expect, test } from '@playwright/test';
import { login } from './utils';

test.describe('AI Assistant Global Dock', () => {
  async function ensureAssistantExpanded(page: any) {
    const container = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    const header = page.locator('[data-testid="assistant-dock-header"], .ai-assistant-container .header').first();
    await expect(container).toBeVisible();
    const state = await container.getAttribute('data-state');
    if (state === 'minimized') {
      await header.click();
    }
    await expect(page.locator('[data-testid="assistant-dock-input"], input[placeholder=\"Ask me anything...\"]').first()).toBeVisible();
  }

  test('is available across main routes and can interact on each page', async ({ page }) => {
    await login(page);
    const container = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    await page.evaluate(() => {
      localStorage.removeItem('ananta.ai-assistant.pending-plan');
      localStorage.removeItem('ananta.ai-assistant.history.v1');
    });
    await page.reload();

    await page.route('**/llm/generate', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { response: 'ok', tool_calls: [] } }),
      });
    });

    await expect(container).toBeVisible();
    await ensureAssistantExpanded(page);

    await page.getByPlaceholder(/Ask me anything/i).fill('hello dashboard');
    await page.getByRole('button', { name: /Send/i }).click();
    await expect(page.locator('.msg-bubble.user-msg', { hasText: 'hello dashboard' })).toBeVisible();

    await page.goto('/settings');
    await ensureAssistantExpanded(page);
    await page.getByPlaceholder(/Ask me anything/i).fill('hello settings');
    await page.getByRole('button', { name: /Send/i }).click();
    await expect(page.locator('.msg-bubble.user-msg', { hasText: 'hello settings' })).toBeVisible();

    await page.goto('/teams');
    await ensureAssistantExpanded(page);
    await page.getByPlaceholder(/Ask me anything/i).fill('hello teams');
    await page.getByRole('button', { name: /Send/i }).click();
    await expect(page.locator('.msg-bubble.user-msg', { hasText: 'hello teams' })).toBeVisible();
  });

  test('uses fullscreen overlay behavior on mobile when expanded', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    const container = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    const header = page.locator('[data-testid="assistant-dock-header"], .ai-assistant-container .header').first();
    await expect(container).toBeVisible();
    await header.click();
    await expect(container).not.toHaveClass(/minimized/);
  });
});
