import { test, expect } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, HUB_URL, getAccessToken, login } from './utils';

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
  async function getJson(path: string, token: string) {
    const res = await fetch(`${HUB_URL}${path}`, {
      headers: { Authorization: `Bearer ${token}` }
    });
    if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
    return await res.json() as any;
  }

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

  test('assistant confirmation creates Scrum templates and role links @requires-llm', async ({ page }) => {
    if (process.env.RUN_LIVE_LLM_TESTS !== '1') {
      test.skip('Requires live LMStudio backend (set RUN_LIVE_LLM_TESTS=1).');
    }
    if (!(await isLlmReachable())) {
      test.skip('LMStudio is not reachable.');
    }
    test.setTimeout(180_000);

    await login(page);
    const token = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    const container = page.locator('.ai-assistant-container');
    await container.locator('.header').click();
    await expect(container.locator('.content')).toBeVisible();

    await container.getByPlaceholder(/Ask me anything|Frage mich etwas/i).fill(
      'Bitte erstelle alle Templates fuer ein Scrum Team.'
    );
    await container.getByRole('button', { name: /Send|Senden/i }).click();

    const toolCard = container.locator('.assistant-msg').filter({ hasText: /Ensure Team Templates|ensure_team_templates/i }).last();
    await expect(toolCard.getByRole('button', { name: /Run Plan|Run|Ausf/i })).toBeVisible({ timeout: 120_000 });
    await toolCard.getByRole('button', { name: /Run Plan|Run|Ausf/i }).click();

    await expect.poll(async () => {
      const res = await getJson('/templates', token);
      const templates = Array.isArray(res?.data) ? res.data : [];
      const names = templates.map((t: any) => String(t?.name || ''));
      return ['Scrum - Product Owner', 'Scrum - Scrum Master', 'Scrum - Developer'].every(n => names.includes(n));
    }, { timeout: 120_000, intervals: [2000, 4000, 8000] }).toBeTruthy();

    await expect.poll(async () => {
      const res = await getJson('/teams/types', token);
      const types = Array.isArray(res?.data) ? res.data : [];
      const scrum = types.find((t: any) => String(t?.name || '').toLowerCase() === 'scrum');
      if (!scrum) return false;
      const roleTemplates = scrum.role_templates;
      return Boolean(roleTemplates && typeof roleTemplates === 'object' && Object.keys(roleTemplates).length >= 3);
    }, { timeout: 120_000, intervals: [2000, 4000, 8000] }).toBeTruthy();
  });
});
