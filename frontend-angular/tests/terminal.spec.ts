import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Terminal', () => {
  test('open terminal, send echo command, receive output', async ({ page }) => {
    await login(page);

    const agentsPromise = page.waitForResponse(res => res.url().includes('/agents') && res.request().method() === 'GET');
    await page.goto('/agents');
    await agentsPromise;

    const hubCard = page.locator('.card').filter({ has: page.locator('strong', { hasText: /^hub$/i }) });
    await hubCard.getByRole('button', { name: /Terminal/i }).click();

    await expect(page.getByRole('heading', { name: /Agent Panel/i })).toBeVisible();
    await expect(page.getByRole('heading', { name: /Live Terminal/i })).toBeVisible();
    await expect(page.getByText(/Status:\s*connected/i)).toBeVisible({ timeout: 15000 });

    const marker = 'terminal-e2e-ok';
    const commandInput = page.getByPlaceholder('echo hello');
    await commandInput.fill(`echo ${marker}`);
    await page.getByRole('button', { name: 'Senden' }).click();

    const outputBuffer = page.getByTestId('terminal-output-buffer');
    await expect(outputBuffer).toContainText(marker, { timeout: 15000 });
  });
});
