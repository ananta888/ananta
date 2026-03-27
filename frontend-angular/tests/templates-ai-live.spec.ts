import { test, expect } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, HUB_URL, getAccessToken, loginFast } from './utils';
import { assistantInput, ensureAssistantExpanded, hasAssistantDock } from './helpers/assistant-dock';

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

  async function waitForFreshAssistantResponse(page: any, baselineCount: number, timeout = 150_000) {
    await expect.poll(
      async () => {
        const messages = page.locator('.assistant-msg');
        const count = await messages.count();
        if (count <= baselineCount) return '';
        return ((await messages.last().textContent()) || '').trim();
      },
      { timeout, intervals: [2000, 5000, 10000] }
    ).not.toEqual('');
  }

  test('responds on templates route via live LLM @requires-llm', async ({ page, request }) => {
    if (process.env.RUN_LIVE_LLM_TESTS !== '1') {
      test.skip('Requires live LMStudio backend (set RUN_LIVE_LLM_TESTS=1).');
    }
    if (!(await isLlmReachable())) {
      test.skip('LMStudio is not reachable.');
    }
    test.setTimeout(180_000);

    await loginFast(page, request);
    await page.goto('/templates', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();
    if (!(await hasAssistantDock(page))) test.skip(true, 'Assistant dock not available in this environment.');
    if (!(await ensureAssistantExpanded(page))) test.skip(true, 'Assistant dock could not be expanded on templates route.');

    const baselineAssistantMessages = await page.locator('.assistant-msg').count();
    await assistantInput(page).fill('Erstelle ein kurzes Scrum-Template fuer einen Product Owner.');
    await page.getByRole('button', { name: /Send|Senden/i }).click();
    await waitForFreshAssistantResponse(page, baselineAssistantMessages);
  });

  test('assistant responds on dashboard and can confirm template plan when proposed @requires-llm', async ({ page, request }) => {
    if (process.env.RUN_LIVE_LLM_TESTS !== '1') {
      test.skip('Requires live LMStudio backend (set RUN_LIVE_LLM_TESTS=1).');
    }
    if (!(await isLlmReachable())) {
      test.skip('LMStudio is not reachable.');
    }
    test.setTimeout(180_000);

    await loginFast(page, request);
    const token = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    if (!(await hasAssistantDock(page))) test.skip(true, 'Assistant dock not available in this environment.');
    if (!(await ensureAssistantExpanded(page))) test.skip(true, 'Assistant dock could not be expanded on dashboard route.');

    const container = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    const baselineAssistantMessages = await container.locator('.assistant-msg').count();
    await assistantInput(page).fill(
      'Bitte erstelle alle Templates fuer ein Scrum Team.'
    );
    await container.getByRole('button', { name: /Send|Senden/i }).click();
    await waitForFreshAssistantResponse(container, baselineAssistantMessages, 150_000);

    const toolCard = container.locator('.assistant-msg').filter({ hasText: /Ensure Team Templates|ensure_team_templates/i }).last();
    const runPlanButton = toolCard.getByRole('button', { name: /Run Plan|Run|Ausf/i });
    if (!(await runPlanButton.isVisible().catch(() => false))) {
      return;
    }
    await toolCard.getByPlaceholder(/Type RUN/i).fill('RUN');
    await runPlanButton.click();

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
