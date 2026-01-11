import { test, expect } from '@playwright/test';

test.describe('Agents Panel', () => {
  test('execute manual command on worker', async ({ page }) => {
    await page.goto('/agents');

    // Open panel for alpha
    const alphaPanelLink = page.getByRole('link', { name: /panel/i }).first();
    await alphaPanelLink.click();

    // Ensure on panel page and input a manual command
    await expect(page.getByRole('heading', { name: /Agent Panel/i })).toBeVisible();
    await page.getByPlaceholder('z. B. echo hello').fill('echo e2e-alpha');

    // Execute
    await page.getByRole('button', { name: 'Ausführen' }).click();

    // Expect output to contain our text
    await expect(page.getByText('Exit:')).toBeVisible();
    await expect(page.getByText('e2e-alpha')).toBeVisible();

    // Logs should now contain the executed command
    await expect(page.getByRole('heading', { name: 'Letzte Logs' })).toBeVisible();
  });
});
