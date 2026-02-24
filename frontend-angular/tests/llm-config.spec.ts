import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('LLM Config', () => {

  async function openLlmSettings(page: any) {
    await login(page);
    await page.goto('/settings');
    await expect(page.getByRole('heading', { name: /System-Einstellungen/i })).toBeVisible();
    await page.getByRole('button', { name: /LLM und KI/i }).click();
    await expect(page.getByText(/Hub LLM Defaults/i)).toBeVisible();
  }

  test('switch provider and save LM Studio mode', async ({ page }) => {
    await openLlmSettings(page);

    const providerSelect = page.getByLabel('Default Provider');
    const openaiUrlInput = page.getByLabel('OpenAI URL');
    const defaultsCard = page.locator('.card', { has: page.getByRole('heading', { name: /Hub LLM Defaults/i }) }).first();
    const saveButton = defaultsCard.getByRole('button', { name: /^Speichern$/i }).first();

    await providerSelect.selectOption('openai');
    await expect(providerSelect).toHaveValue('openai');
    await openaiUrlInput.fill('https://api.openai.com/v1/chat/completions');
    await saveButton.click();

    await providerSelect.selectOption('lmstudio');
    await expect(providerSelect).toHaveValue('lmstudio');
    await saveButton.click();
  });

  test('save codex provider defaults', async ({ page }) => {
    await openLlmSettings(page);

    const hubRow = page.locator('tr', { has: page.getByText(/hub \(hub\)/i) }).first();
    await expect(hubRow).toBeVisible();
    const providerSelect = hubRow.locator('select').first();
    await providerSelect.selectOption('codex');
    await expect(providerSelect).toHaveValue('codex');
    await hubRow.getByRole('button', { name: /^Speichern$/i }).click();
  });
});
