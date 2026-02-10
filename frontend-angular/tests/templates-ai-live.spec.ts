import { test, expect } from '@playwright/test';
import { login } from './utils';

async function isLlmReachable() {
  const baseUrl = process.env.LMSTUDIO_URL || 'http://localhost:1234/v1';
  const url = baseUrl.endsWith('/v1') ? `${baseUrl}/models` : baseUrl;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 3000);
  try {
    const res = await fetch(url, { signal: controller.signal });
    return res.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timeoutId);
  }
}

test.describe('Templates AI (Live LMStudio)', () => {
  test('generates draft via live LLM @requires-llm', async ({ page }) => {
    if (process.env.RUN_LIVE_LLM_TESTS !== '1') {
      test.skip('Requires live LMStudio backend (set RUN_LIVE_LLM_TESTS=1).');
    }
    if (!(await isLlmReachable())) {
      test.skip('LMStudio is not reachable.');
    }
    test.setTimeout(180_000);

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
    // Increased timeout to 150s for slower local LLMs
    await expect.poll(async () => {
      const name = (await nameInput.inputValue()).trim();
      const desc = (await descInput.inputValue()).trim();
      const prompt = (await promptArea.inputValue()).trim();
      return Boolean(name || desc || prompt);
    }, { 
      timeout: 150_000, 
      intervals: [2000, 5000, 10000] 
    }).toBeTruthy();
  });
});
