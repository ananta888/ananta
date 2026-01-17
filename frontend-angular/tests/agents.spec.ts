import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Agents Panel', () => {
  test('execute manual command on worker', async ({ page }) => {
    await login(page);
    await page.goto('/agents');

    // Open panel for alpha
    const alphaCard = page.locator('.card').filter({ hasText: 'alpha' });
    await alphaCard.getByRole('link', { name: 'Panel' }).click();

    // Ensure on panel page and input a manual command
    await expect(page.getByRole('heading', { name: /Agent Panel/i })).toBeVisible();
    await page.getByPlaceholder('z. B. echo hello').fill('echo e2e-alpha');

    // Execute
    await page.getByRole('button', { name: 'Ausführen' }).click();

    // Expect output to contain our text
    await expect(page.getByText('Exit:')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('pre')).toContainText('e2e-alpha');

    // Logs should now contain the executed command
    await page.getByRole('button', { name: 'Logs' }).click();
    await expect(page.getByRole('heading', { name: 'Letzte Logs' })).toBeVisible();
  });
});
