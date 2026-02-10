import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Agents Panel', () => {
  test('open panel and edit manual command on worker', async ({ page }) => {
    await login(page);

    const agentsPromise = page.waitForResponse(res => res.url().includes('/agents') && res.request().method() === 'GET');
    await page.goto('/agents');
    await agentsPromise;

    const alphaCard = page.locator('.card').filter({ hasText: 'alpha' });
    await alphaCard.getByRole('link', { name: 'Panel' }).click();

    await expect(page.getByRole('heading', { name: /Agent Panel/i })).toBeVisible();
    const commandInput = page.getByPlaceholder('z. B. echo hello');
    await commandInput.fill('echo e2e-alpha');

    await expect(commandInput).toHaveValue('echo e2e-alpha');
    await expect(page.getByRole('button', { name: /Ausf/i })).toBeEnabled();
  });

  test('show proposal controls on agent panel', async ({ page }) => {
    await login(page);

    const agentsPromise = page.waitForResponse(res => res.url().includes('/agents') && res.request().method() === 'GET');
    await page.goto('/agents');
    await agentsPromise;

    const alphaCard = page.locator('.card').filter({ hasText: 'alpha' });
    await alphaCard.getByRole('link', { name: 'Panel' }).click();

    const promptInput = page.getByPlaceholder(/REASON\/COMMAND/i);
    await promptInput.fill('Bitte schlage einen Befehl vor');

    await expect(promptInput).toHaveValue('Bitte schlage einen Befehl vor');
    await expect(page.getByRole('button', { name: /Vorschlag/i })).toBeEnabled();
  });
});
