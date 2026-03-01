import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('LLM Config', () => {
  test.describe.configure({ timeout: 120000 });

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

  test('saves per-agent context_limit and shows catalog context lengths', async ({ page }) => {
    let postedContextLimit: number | null = null;
    await page.route('**/config', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      try {
        const body = route.request().postDataJSON() as any;
        postedContextLimit = Number(body?.llm_config?.context_limit);
      } catch {
        postedContextLimit = null;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: {} }),
      });
    });

    await page.route('**/providers/catalog*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            default_provider: 'lmstudio',
            default_model: 'model-ctx-32k',
            providers: [
              {
                provider: 'lmstudio',
                base_url: 'http://127.0.0.1:1234/v1',
                available: true,
                model_count: 2,
                models: [
                  { id: 'model-ctx-32k', display_name: 'model-ctx-32k', context_length: 32768, selected: true },
                  { id: 'model-ctx-8k', display_name: 'model-ctx-8k', context_length: 8192, selected: false }
                ],
                capabilities: { dynamic_models: true, supports_chat: true }
              }
            ]
          }
        })
      });
    });

    await openLlmSettings(page);

    // 1) Catalog context length is visible in model select
    const modelSelect = page.getByLabel('Default Model').first();
    await expect.poll(async () => {
      const options = await modelSelect.locator('option').allTextContents();
      return options.join(' | ');
    }, { timeout: 10000 }).toContain('(ctx 32768)');

    // 2) Per-agent context_limit is persisted via /config POST
    const hubRow = page.locator('tr', { has: page.getByText(/hub \(hub\)/i) }).first();
    await expect(hubRow).toBeVisible();

    // Ctx column is the 5th cell in the row.
    const contextLimitInput = hubRow.locator('td').nth(4).locator('input');
    await contextLimitInput.fill('12288');
    await expect(contextLimitInput).toHaveValue('12288');

    await hubRow.getByRole('button', { name: /^Speichern$/i }).click();
    await expect.poll(() => postedContextLimit, { timeout: 10000 }).toBe(12288);
  });
});
