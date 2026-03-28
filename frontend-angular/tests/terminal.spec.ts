import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Terminal', () => {
  test('open hub terminal and keep live tracking across reconnect', async ({ page }) => {
    await login(page);

    const agentsPromise = page.waitForResponse(res => res.url().includes('/agents') && res.request().method() === 'GET');
    await page.goto('/agents');
    await agentsPromise;

    const hubCard = page.locator('.card').filter({ has: page.locator('strong', { hasText: /^hub$/i }) });
    await hubCard.getByRole('button', { name: /Terminal/i }).click();

    await expect(page.getByRole('heading', { name: /Agent Panel/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Live Terminal/i })).toBeVisible();

    const outputBuffer = page.getByTestId('terminal-output-buffer');
    await expect(outputBuffer).toBeAttached();

    await page.getByRole('button', { name: /Leeren/i }).click();
    await expect(outputBuffer).toHaveText('');

    await page.getByRole('button', { name: /Neu verbinden/i }).click();
    await expect(page.getByRole('heading', { name: /Live Terminal/i })).toBeVisible();
    await expect(page.getByTestId('terminal-output-buffer')).toBeAttached();
  });

  test('terminal is reachable for hub alpha and beta', async ({ page }) => {
    await login(page);

    const agentsPromise = page.waitForResponse(res => res.url().includes('/agents') && res.request().method() === 'GET');
    await page.goto('/agents');
    await agentsPromise;

    for (const name of ['hub', 'alpha', 'beta']) {
      const card = page.locator('.card').filter({ has: page.locator('strong', { hasText: new RegExp(`^${name}$`, 'i') }) });
      await card.getByRole('button', { name: /Terminal/i }).click();

      await expect(page.getByRole('heading', { name: /Live Terminal/i })).toBeVisible();
      await expect(page.locator('.status-pill')).toContainText(/Status:\s*connected/i, { timeout: 30000 });
      await expect(page.getByTestId('terminal-output-buffer')).toContainText(/connected:|job control turned off|#\s*$/i, { timeout: 30000 });

      await page.goto('/agents');
      await expect(page.locator('.card').filter({ hasText: name }).first()).toBeVisible();
    }
  });
});
