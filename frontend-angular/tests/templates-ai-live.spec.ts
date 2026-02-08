import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Templates AI (Live LMStudio)', () => {
  test('generates draft via live LLM', async ({ page }) => {
    test.setTimeout(120_000);

    await login(page);
    await page.goto('/templates');

    await page.getByPlaceholder(/Beschreibe das Template/i).fill(
      'bitte erstelle alle templates für ein scrum team'
    );
    await page.getByRole('button', { name: /Entwurf/i }).click();

    const nameInput = page.getByPlaceholder('Name');
    const descInput = page.getByPlaceholder('Beschreibung');
    const promptArea = page.getByLabel('Prompt Template');

    // Expect at least one field to be filled by the LLM response.
    await expect.poll(async () => {
      const name = (await nameInput.inputValue()).trim();
      const desc = (await descInput.inputValue()).trim();
      const prompt = (await promptArea.inputValue()).trim();
      return Boolean(name || desc || prompt);
    }, { timeout: 90_000, intervals: [1000, 2000, 3000, 5000] }).toBeTruthy();
  });
});
