import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Terminal', () => {
  test('open terminal and keep live tracking across reconnect', async ({ page }) => {
    await login(page);

    const agentsPromise = page.waitForResponse(res => res.url().includes('/agents') && res.request().method() === 'GET');
    await page.goto('/agents');
    await agentsPromise;

    const hubCard = page.locator('.card').filter({ has: page.locator('strong', { hasText: /^hub$/i }) });
    await hubCard.getByRole('button', { name: /Terminal/i }).click();

    await expect(page.getByRole('heading', { name: /Agent Panel/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Live Terminal/i })).toBeVisible();

    const outputBuffer = page.getByTestId('terminal-output-buffer');
    await expect(outputBuffer).toContainText(/job control turned off|# /i, { timeout: 30000 });

    await page.getByRole('button', { name: /Leeren/i }).click();
    await expect(outputBuffer).toHaveText('');

    await page.getByRole('button', { name: /Neu verbinden/i }).click();
    await expect(page.getByRole('heading', { name: /Live Terminal/i })).toBeVisible();
    await expect(page.getByTestId('terminal-output-buffer')).toBeAttached();
  });
});
