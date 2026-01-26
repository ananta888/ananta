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
    await page.getByRole('button', { name: /Ausf/i }).click();

    // Expect output to contain our text
    await expect(page.getByText('Exit:')).toBeVisible({ timeout: 20000 });
    await expect(page.locator('pre')).toContainText('e2e-alpha');

    // Logs should now contain the executed command
    await page.getByRole('button', { name: 'Logs' }).click();
    await expect(page.getByRole('heading', { name: 'Letzte Logs' })).toBeVisible();
  });

  test('propose and execute via agent panel', async ({ page }) => {
    await login(page);
    await page.route('**/step/propose', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ reason: 'Use echo for test', command: 'echo e2e-proposed' })
      });
    });
    await page.route('**/step/execute', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ output: 'e2e-proposed', exit_code: 0 })
      });
    });
    await page.route('**/logs?*', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([{ command: 'echo e2e-proposed', returncode: 0 }])
      });
    });

    await page.goto('/agents');
    const alphaCard = page.locator('.card').filter({ hasText: 'alpha' });
    await alphaCard.getByRole('link', { name: 'Panel' }).click();

    await page.getByPlaceholder(/REASON\/COMMAND/i).fill('Bitte schlage einen Befehl vor');
    await page.getByRole('button', { name: /Vorschlag/i }).click();

    await expect(page.getByText('Use echo for test')).toBeVisible();
    await expect(page.getByText('echo e2e-proposed')).toBeVisible();

    await page.getByRole('button', { name: /Ausf/i }).click();
    await expect(page.getByText('Exit:')).toBeVisible();
    await expect(page.locator('pre')).toContainText('e2e-proposed');

    await page.getByRole('button', { name: 'Logs' }).click();
    await expect(page.getByText('echo e2e-proposed')).toBeVisible();
  });
});
