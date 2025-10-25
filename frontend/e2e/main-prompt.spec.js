import { test, expect } from '@playwright/test';

// E2E: Global MAIN_PROMPT configuration and fallback behavior
// - Set global main_prompt via UI (Settings tab)
// - Create an agent without explicit prompt/template → /next-config returns global main_prompt
// - Create an agent with explicit prompt → /next-config returns explicit prompt (takes precedence)
// - Cleanup agents and reset main_prompt to empty

test('Global MAIN_PROMPT fallback and precedence', async ({ page, request }) => {
  const uid = Date.now();
  const mainPrompt = `Global MAIN_PROMPT ${uid}`;
  const agentNoPrompt = `e2e-mp-noprompt-${uid}`;
  const agentWithPrompt = `e2e-mp-withprompt-${uid}`;
  const explicitPrompt = `Explicit ${uid}`;

  // Navigate to UI and open Einstellungen (Settings) tab
  await page.goto('/ui/');
  await page.waitForLoadState('networkidle');
  await page.click('[data-testid="tab-einstellungen"]');

  // Wait for /config and settings to load
  await page.waitForResponse(r => r.url().endsWith('/config') && r.ok());

  // Set global MAIN_PROMPT via UI
  await page.fill('[data-test="main-prompt-input"]', mainPrompt);
  await page.click('[data-test="save-main-prompt"]');

  // Verify backend reflects main_prompt
  const cfg1 = await (await request.get('/config')).json();
  expect(cfg1.main_prompt).toBe(mainPrompt);

  // Go to Agents tab
  await page.click('[data-testid="tab-agents"]');
  await page.waitForResponse(r => r.url().endsWith('/config') && r.ok());

  // Helper to add an agent via UI
  async function addAgent(name, { prompt, template } = {}) {
    await page.fill('[data-test="new-name"]', name);
    await page.fill('input[placeholder="model.name"]', 'm1');
    // Select at least one model if available
    try {
      await page.selectOption('[data-test="new-models"]', ['m1']);
    } catch {}
    if (template) {
      await page.selectOption('[data-test="new-template"]', template);
    }
    if (prompt) {
      await page.fill('[data-test="new-prompt"]', prompt);
    }
    await page.click('[data-test="add"]');
    // Wait until config contains the agent
    await page.waitForFunction(async (name) => {
      const res = await fetch('/config');
      if (!res.ok) return false;
      const json = await res.json();
      return json.agents && Object.prototype.hasOwnProperty.call(json.agents, name);
    }, name, { timeout: 15000 });
  }

  // Add both agents
  await addAgent(agentNoPrompt, {});
  await addAgent(agentWithPrompt, { prompt: explicitPrompt });

  // Verify /next-config resolution
  const resNoPrompt = await request.get(`/next-config?agent=${encodeURIComponent(agentNoPrompt)}`);
  expect(resNoPrompt.ok()).toBeTruthy();
  const jsonNoPrompt = await resNoPrompt.json();
  expect(jsonNoPrompt.prompt).toBe(mainPrompt);

  const resWithPrompt = await request.get(`/next-config?agent=${encodeURIComponent(agentWithPrompt)}`);
  expect(resWithPrompt.ok()).toBeTruthy();
  const jsonWithPrompt = await resWithPrompt.json();
  expect(jsonWithPrompt.prompt).toBe(explicitPrompt);

  // Cleanup helpers
  async function deleteAgent(name) {
    const row = page.locator('tbody tr', { hasText: name });
    await expect(row).toBeVisible({ timeout: 15000 });
    await row.locator('[data-test="delete"]').click();
    await page.waitForFunction(async (name) => {
      const res = await fetch('/config');
      if (!res.ok) return false;
      const json = await res.json();
      return !json.agents || !Object.prototype.hasOwnProperty.call(json.agents, name);
    }, name, { timeout: 15000 });
  }

  await deleteAgent(agentNoPrompt);
  await deleteAgent(agentWithPrompt);

  // Reset global main_prompt to empty to avoid leaking state
  await request.post('/config/main_prompt', { data: { main_prompt: '' } });
});
