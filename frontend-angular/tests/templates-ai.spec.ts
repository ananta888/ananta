import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Templates AI', () => {
  test('generate template draft via LM Studio', async ({ page }) => {
    await login(page);
    await page.goto('/templates');

    await page.getByPlaceholder(/Beschreibe das Template/i).fill(
      'Ein Template fuer API Fehlerbehandlung mit klaren Schritten und Beispielen.'
    );
    await page.getByRole('button', { name: /Entwurf/i }).click();

    const nameInput = page.getByPlaceholder('Name');
    const descInput = page.getByPlaceholder('Beschreibung');
    const promptArea = page.getByPlaceholder(/Prompt Template/i);

    await expect.poll(async () => (await nameInput.inputValue()).trim().length, {
      timeout: 60000
    }).toBeGreaterThan(2);

    await expect.poll(async () => (await descInput.inputValue()).trim().length, {
      timeout: 60000
    }).toBeGreaterThan(2);

    await expect.poll(async () => (await promptArea.inputValue()).trim().length, {
      timeout: 60000
    }).toBeGreaterThan(20);
  });
});
