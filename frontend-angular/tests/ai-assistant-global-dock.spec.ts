import { expect, test } from '@playwright/test';
import { login } from './utils';

test.describe('AI Assistant Global Dock', () => {
  test('is available across main routes and can interact on each page', async ({ page }) => {
    await login(page);
    const container = page.locator('.ai-assistant-container');
    const header = container.locator('.header');

    await expect(container).toBeVisible();
    await header.click();

    await page.getByPlaceholder(/Ask me anything/i).fill('hello dashboard');
    await page.getByRole('button', { name: /Send/i }).click();
    await expect(page.locator('.msg-bubble.user-msg', { hasText: 'hello dashboard' })).toBeVisible();

    await page.goto('/settings');
    await expect(container).toBeVisible();
    await page.getByPlaceholder(/Ask me anything/i).fill('hello settings');
    await page.getByRole('button', { name: /Send/i }).click();
    await expect(page.locator('.msg-bubble.user-msg', { hasText: 'hello settings' })).toBeVisible();

    await page.goto('/teams');
    await expect(container).toBeVisible();
    await page.getByPlaceholder(/Ask me anything/i).fill('hello teams');
    await page.getByRole('button', { name: /Send/i }).click();
    await expect(page.locator('.msg-bubble.user-msg', { hasText: 'hello teams' })).toBeVisible();
  });

  test('uses fullscreen overlay behavior on mobile when expanded', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    const container = page.locator('.ai-assistant-container');
    const header = container.locator('.header');
    await expect(container).toBeVisible();
    await header.click();
    await expect(container).not.toHaveClass(/minimized/);
  });
});
