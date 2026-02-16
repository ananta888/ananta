import { test, expect } from '@playwright/test';
import { HUB_URL, login } from './utils';

test.describe('Settings Config', () => {
  async function getHubInfo(page: any) {
    return page.evaluate((defaultHubUrl: string) => {
      const token = localStorage.getItem('ananta.user.token');
      const raw = localStorage.getItem('ananta.agents.v1');
      let hubUrl = defaultHubUrl;
      if (raw) {
        try {
          const agents = JSON.parse(raw);
          const hub = agents.find((a: any) => a.role === 'hub');
          if (hub?.url) hubUrl = hub.url;
        } catch {}
      }
      if (!hubUrl || hubUrl === 'undefined') hubUrl = defaultHubUrl;
      return { hubUrl, token };
    }, HUB_URL);
  }

  test('loads, saves, and validates raw config', async ({ page, request }) => {
    const seededConfig: any = {
      default_provider: 'openai',
      default_model: 'gpt-4o',
      http_timeout: 20
    };

    await login(page);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const seedRes = await request.post(`${hubUrl}/config`, { headers, data: seededConfig });
    expect(seedRes.ok()).toBeTruthy();

    await page.goto('/settings');

    const rawCard = page.locator('.card', { has: page.getByRole('heading', { name: /Roh-Konfiguration/i }) });
    const rawArea = rawCard.locator('textarea');

    const updated = { ...seededConfig, http_timeout: 42, command_timeout: 30 };
    await rawArea.fill(JSON.stringify(updated, null, 2));

    const configPostPromise1 = page.waitForResponse(res => res.url().includes('/config') && res.request().method() === 'POST' && res.status() === 200);
    await rawCard.getByRole('button', { name: /Roh-Daten Speichern/i }).click();
    await configPostPromise1;

    const verifyRes = await request.get(`${hubUrl}/config`, { headers });
    expect(verifyRes.ok()).toBeTruthy();
    const verifyBody = await verifyRes.json();
    const verified = verifyBody?.data || verifyBody;
    expect(verified.http_timeout).toBe(42);
    expect(verified.command_timeout).toBe(30);

    await rawArea.fill('{');
    await rawCard.getByRole('button', { name: /Roh-Daten Speichern/i }).click();

    await expect(page.locator('.notification.error', { hasText: /JSON/i })).toBeVisible();
  });

  test('switches settings sections and persists quality/system changes', async ({ page, request }) => {
    const seededConfig: any = {
      default_provider: 'openai',
      default_model: 'gpt-4o-mini',
      http_timeout: 20,
      command_timeout: 25,
      quality_gates: {
        enabled: true,
        autopilot_enforce: true,
        min_output_chars: 11,
        coding_keywords: ['code', 'test'],
        required_output_markers_for_coding: ['pytest', 'passed']
      }
    };

    await login(page);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
    const seedRes = await request.post(`${hubUrl}/config`, { headers, data: seededConfig });
    expect(seedRes.ok()).toBeTruthy();

    await page.goto('/settings');

    const accountTab = page.locator('button.button-outline', { hasText: /^Account$/i });
    const llmTab = page.locator('button.button-outline', { hasText: /LLM/i });
    const qualityTab = page.locator('button.button-outline', { hasText: /^Quality Gates$/i });
    const systemTab = page.locator('button.button-outline', { hasText: /^System$/i });

    await expect(llmTab).toBeVisible();
    await expect(page.getByRole('heading', { name: /Hub LLM Defaults/i })).toBeVisible();

    await qualityTab.click();
    await expect(page.getByRole('heading', { name: /^Quality Gates$/i })).toBeVisible();
    await page.locator('label:has-text("Min. Output Zeichen") input[type="number"]').fill('27');
    const qualitySaveResponse = page.waitForResponse(res =>
      res.url().includes('/config') && res.request().method() === 'POST' && res.status() === 200
    );
    await page.getByRole('button', { name: /Save Quality Gates/i }).click();
    await qualitySaveResponse;

    await systemTab.click();
    await expect(page.getByRole('heading', { name: /System Parameter/i })).toBeVisible();
    const httpTimeout = page.locator('label:has-text("HTTP Timeout (s)") input[type="number"]');
    await httpTimeout.fill('41');
    const systemSaveResponse = page.waitForResponse(res =>
      res.url().includes('/config') && res.request().method() === 'POST' && res.status() === 200
    );
    await page.getByRole('button', { name: /^Speichern$/i }).first().click();
    await systemSaveResponse;

    await llmTab.click();
    await expect(page.getByRole('heading', { name: /Hub LLM Defaults/i })).toBeVisible();
    await accountTab.click();
    await expect(accountTab).toHaveClass(/active-toggle/);
    await systemTab.click();
    await expect(systemTab).toHaveClass(/active-toggle/);
    await page.getByRole('button', { name: /Aktualisieren/i }).click();
    await expect(httpTimeout).toHaveValue('41');

    const verifyRes = await request.get(`${hubUrl}/config`, { headers });
    expect(verifyRes.ok()).toBeTruthy();
    const verifyBody = await verifyRes.json();
    const verified = verifyBody?.data || verifyBody;
    expect(verified.http_timeout).toBe(41);
    expect(verified.quality_gates?.min_output_chars).toBe(27);
  });
});
