import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('LLM Config', () => {
  test('switch provider and save LM Studio mode', async ({ page }) => {
    await login(page);
    
    const agentsPromise = page.waitForResponse(res => res.url().includes('/agents') && res.request().method() === 'GET');
    await page.goto('/agents');
    await agentsPromise;

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
    await expect(modeSelect).toBeVisible();
    await modeSelect.selectOption('completions');
    await Promise.all([
      page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'POST' && res.ok()),
      saveLlm.click()
    ]);

    const authInfo = await page.evaluate(() => {
      const token = localStorage.getItem('ananta.user.token') || '';
      const raw = localStorage.getItem('ananta.agents.v1');
      let hubUrl = 'http://localhost:5500';
      if (raw) {
        try {
          const agents = JSON.parse(raw);
          const hub = agents.find((a: any) => a.role === 'hub');
          if (hub?.url) hubUrl = hub.url;
        } catch {}
      }
      return { token, hubUrl };
    });
    const cfgRes = await page.request.get(`${authInfo.hubUrl}/config`, {
      headers: authInfo.token ? { Authorization: `Bearer ${authInfo.token}` } : undefined
    });
    expect(cfgRes.ok()).toBeTruthy();
    const cfgBody = await cfgRes.json();
    const cfgData = cfgBody?.data ?? cfgBody;
    expect(cfgData?.llm_config?.provider).toBe('lmstudio');
    expect(cfgData?.llm_config?.lmstudio_api_mode).toBe('completions');

    await page.reload({ waitUntil: 'domcontentloaded' });
    await page.getByRole('button', { name: /^Konfiguration$/i }).click();
    const llmTabButton = page.getByRole('button', { name: /^LLM$/i });
    await expect(llmTabButton).toBeVisible();
    await llmTabButton.click();
    await expect(providerSelect).toBeVisible();
    await expect(saveLlm).toBeVisible();
    if (await modeSelect.count()) {
      await expect(modeSelect).toHaveValue('completions');
    }
  });
});
