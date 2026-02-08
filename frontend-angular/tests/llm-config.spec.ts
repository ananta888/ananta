import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('LLM Config', () => {
  test('switch provider and persist LM Studio mode', async ({ page }) => {
    await login(page);
    await page.goto('/agents');

    const hubCard = page.locator('.card', { has: page.getByText('(hub)') }).first();
    await expect(hubCard).toHaveCount(1);
    await hubCard.getByRole('link', { name: /Panel/i }).click();

    await page.getByRole('button', { name: /^Konfiguration$/i }).click();
    const configArea = page.locator('textarea');
    if ((await configArea.inputValue()).trim() === '') {
      await configArea.fill('{}');
    }

    await page.getByRole('button', { name: /^LLM$/i }).click();

    const providerSelect = page.getByLabel('Provider');
    const modelInput = page.getByLabel('Model');
    const baseUrlInput = page.getByLabel(/Base URL/i);
    const apiKeyInput = page.getByLabel(/API Key/i);

    await providerSelect.selectOption('openai');
    await modelInput.fill('gpt-4o-mini');
    await baseUrlInput.fill('');
    await apiKeyInput.fill('e2e-test-key');
    const saveLlm = page.getByRole('button', { name: /LLM Speichern/i });
    await Promise.all([
      page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'POST' && res.ok()),
      saveLlm.click()
    ]);

    await providerSelect.selectOption('lmstudio');
    await modelInput.fill('e2e-lmstudio');
    await baseUrlInput.fill('http://192.168.56.1:1234/v1');
    await apiKeyInput.fill('e2e-lmstudio-key');

    const modeSelect = page.getByLabel('LM Studio Modus');
    await modeSelect.selectOption('completions');
    await Promise.all([
      page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'POST' && res.ok()),
      saveLlm.click()
    ]);

    await page.reload();
    await page.getByRole('button', { name: /^LLM$/i }).click();

    await expect(providerSelect).toHaveValue('lmstudio');
    await expect(modelInput).toHaveValue('e2e-lmstudio');
    await expect(baseUrlInput).toHaveValue('http://192.168.56.1:1234/v1');
    await expect(modeSelect).toHaveValue('completions');
  });
});
