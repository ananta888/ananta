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
    await openaiUrlInput.fill('https://api.openai.com/v1/chat/completions');
    await Promise.all([
      page.waitForRequest((req) => {
        if (!req.url().includes('/config') || req.method() !== 'POST') return false;
        try {
          const body = JSON.parse(req.postData() || '{}');
          return body?.default_provider === 'openai';
        } catch {
          return false;
        }
      }),
      saveButton.click()
    ]);

    await providerSelect.selectOption('lmstudio');
    await Promise.all([
      page.waitForRequest((req) => {
        if (!req.url().includes('/config') || req.method() !== 'POST') return false;
        try {
          const body = JSON.parse(req.postData() || '{}');
          return body?.default_provider === 'lmstudio';
        } catch {
          return false;
        }
      }),
      saveButton.click()
    ]);
  });

  test('save codex provider defaults', async ({ page }) => {
    await openLlmSettings(page);

    const hubRow = page.locator('tr', { has: page.getByText(/hub \(hub\)/i) }).first();
    await expect(hubRow).toBeVisible();
    const providerSelect = hubRow.locator('select').first();
    await providerSelect.selectOption('codex');
    await Promise.all([
      page.waitForRequest((req) => {
        if (!req.url().includes('/config') || req.method() !== 'POST') return false;
        const body = req.postData() || '';
        return body.includes('"llm_config"') && body.includes('"provider":"codex"');
      }),
      hubRow.getByRole('button', { name: /^Speichern$/i }).click()
    ]);
  });
});
