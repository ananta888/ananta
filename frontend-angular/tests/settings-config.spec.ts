import { test, expect } from '@playwright/test';
import { HUB_URL, login } from './utils';

test.describe('Settings Config', () => {
  test.setTimeout(120000);

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

  async function waitForConfigValue(
    request: any,
    hubUrl: string,
    headers: Record<string, string> | undefined,
    predicate: (cfg: any) => boolean,
    timeoutMs = 10000
  ) {
    const started = Date.now();
    while ((Date.now() - started) < timeoutMs) {
      const res = await request.get(`${hubUrl}/config`, { headers });
      if (res.ok()) {
        const body = await res.json();
        const cfg = body?.data || body;
        if (predicate(cfg)) return cfg;
      }
      await new Promise(r => setTimeout(r, 250));
    }
    throw new Error('Timed out waiting for expected /config value');
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
    await page.locator('button.button-outline', { hasText: /^System$/i }).click();

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

    let invalidConfigPostSeen = false;
    await page.route('**/config', async (route) => {
      if (route.request().method() === 'POST') {
        const payload = route.request().postData() || '';
        if (payload.trim() === '{') {
          invalidConfigPostSeen = true;
        }
      }
      await route.continue();
    });
    await rawArea.fill('{');
    await rawCard.getByRole('button', { name: /Roh-Daten Speichern/i }).click();
    await page.waitForTimeout(500);
    expect(invalidConfigPostSeen).toBeFalsy();
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
    const qualityTab = page.locator('button.button-outline', { hasText: /^Qualitaetsregeln$/i });
    const systemTab = page.locator('button.button-outline', { hasText: /^System$/i });

    await expect(llmTab).toBeVisible();
    await expect(page.getByRole('heading', { name: /Hub LLM Defaults/i })).toBeVisible();

    await qualityTab.click();
    await expect(page.getByRole('heading', { name: /^Qualitaetsregeln/i })).toBeVisible();
    await page.locator('label:has-text("Min. Output Zeichen") input[type="number"]').fill('27');
    await page.getByRole('button', { name: /Qualitaetsregeln speichern/i }).click();
    await waitForConfigValue(request, hubUrl, headers, (cfg: any) => Number(cfg?.quality_gates?.min_output_chars) === 27);

    await systemTab.click();
    await expect(page.getByRole('heading', { name: /System Parameter/i })).toBeVisible();
    const httpTimeout = page.locator('label:has-text("HTTP Timeout (s)") input[type="number"]');
    await httpTimeout.fill('41');
    await page.locator('.card', { has: page.getByRole('heading', { name: /System Parameter/i }) })
      .getByRole('button', { name: /^Speichern$/i })
      .click();
    await waitForConfigValue(request, hubUrl, headers, (cfg: any) => Number(cfg?.http_timeout) === 41);

    await llmTab.click();
    await expect(page.getByRole('heading', { name: /Hub LLM Defaults/i })).toBeVisible();
    await accountTab.click();
    await expect(accountTab).toHaveClass(/active-toggle/);
    await systemTab.click();
    await expect(systemTab).toHaveClass(/active-toggle/);
    await page.getByRole('button', { name: /Aktualisieren/i }).click();
    await expect(httpTimeout).toHaveValue('41');

    const verified = await waitForConfigValue(
      request,
      hubUrl,
      headers,
      (cfg: any) => Number(cfg?.http_timeout) === 41 && Number(cfg?.quality_gates?.min_output_chars) === 27,
      12000
    );
    expect(verified.http_timeout).toBe(41);
    expect(verified.quality_gates?.min_output_chars).toBe(27);
  });

  test('keeps API/UI config roundtrip consistent across refresh and reload', async ({ page, request }) => {
    const marker = `e2e-roundtrip-${Date.now()}`;
    const seededConfig: any = {
      default_provider: 'openai',
      default_model: 'gpt-4o-mini',
      http_timeout: 17,
      command_timeout: 23,
      quality_gates: {
        enabled: true,
        autopilot_enforce: true,
        min_output_chars: 19,
        coding_keywords: ['code', 'test'],
        required_output_markers_for_coding: ['pytest', 'passed'],
      },
      e2e_roundtrip_marker: marker,
    };

    await login(page);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const seedRes = await request.post(`${hubUrl}/config`, { headers, data: seededConfig });
    expect(seedRes.ok()).toBeTruthy();

    await page.goto('/settings');
    const systemTab = page.locator('button.button-outline', { hasText: /^System$/i });
    const qualityTab = page.locator('button.button-outline', { hasText: /^Qualitaetsregeln$/i });
    await systemTab.click();
    await page.getByRole('button', { name: /Aktualisieren/i }).click();

    const httpTimeout = page.locator('label:has-text("HTTP Timeout (s)") input[type="number"]');
    const commandTimeout = page.locator('label:has-text("Command Timeout (s)") input[type="number"]');
    await expect.poll(async () => await httpTimeout.inputValue(), { timeout: 15000 }).toBe('17');
    await expect.poll(async () => await commandTimeout.inputValue(), { timeout: 15000 }).toBe('23');

    const rawCard = page.locator('.card', { has: page.getByRole('heading', { name: /Roh-Konfiguration/i }) });
    const rawArea = rawCard.locator('textarea');
    await expect(rawArea).toBeVisible();
    const initialRaw = JSON.parse((await rawArea.inputValue()) || '{}');
    expect(initialRaw.e2e_roundtrip_marker).toBe(marker);

    await httpTimeout.fill('44');
    await commandTimeout.fill('45');
    await page.locator('.card', { has: page.getByRole('heading', { name: /System Parameter/i }) })
      .getByRole('button', { name: /^Speichern$/i })
      .click();
    await waitForConfigValue(
      request,
      hubUrl,
      headers,
      (cfg: any) => Number(cfg?.http_timeout) === 44 && Number(cfg?.command_timeout) === 45,
      12000
    );

    await qualityTab.click();
    const minChars = page.locator('label:has-text("Min. Output Zeichen") input[type="number"]');
    await expect(minChars).toHaveValue('19');
    await minChars.fill('37');
    await page.getByRole('button', { name: /Qualitaetsregeln speichern/i }).click();
    await waitForConfigValue(
      request,
      hubUrl,
      headers,
      (cfg: any) => Number(cfg?.quality_gates?.min_output_chars) === 37,
      12000
    );

    await page.reload();
    await systemTab.click();
    await expect(httpTimeout).toHaveValue('44');
    await expect(commandTimeout).toHaveValue('45');
    const reloadedRaw = JSON.parse((await rawArea.inputValue()) || '{}');
    expect(reloadedRaw.e2e_roundtrip_marker).toBe(marker);

    const verifyRes = await request.get(`${hubUrl}/config`, { headers });
    expect(verifyRes.ok()).toBeTruthy();
    const verifyBody = await verifyRes.json();
    const verified = verifyBody?.data || verifyBody;
    expect(Number(verified.http_timeout)).toBe(44);
    expect(Number(verified.command_timeout)).toBe(45);
    expect(Number(verified?.quality_gates?.min_output_chars)).toBe(37);
    expect(String(verified.e2e_roundtrip_marker || '')).toBe(marker);
  });
});
