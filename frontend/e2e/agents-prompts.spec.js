import { test, expect } from '@playwright/test';

// This E2E verifies that per-agent prompts are persisted via the UI (Agents.vue)
// and resolved by the controller via GET /next-config?agent=<name>.
// It also verifies template fallback when no explicit prompt is set.

test.describe('Multi-Agent prompt configuration', () => {
  test('Explicit prompt per agent and template fallback', async ({ page, request }) => {
    // Create unique names to avoid collisions and allow cleanup
    const uid = Date.now();
    const agentA = `e2e-agentA-${uid}`;
    const agentB = `e2e-agentB-${uid}`;
    const agentTpl = `e2e-agentTpl-${uid}`;

    const promptA = `Prompt A ${uid}`;
    const promptB = `Prompt B ${uid}`;

    // Ensure we have at least one template to use for fallback
    const cfgRes = await request.get('/config');
    expect(cfgRes.ok()).toBeTruthy();
    const cfg = await cfgRes.json();
    const templates = cfg.prompt_templates || {};
    const templateNames = Object.keys(templates);
    expect(templateNames.length).toBeGreaterThan(0);
    const tplName = templateNames[0];
    const tplText = templates[tplName];

    // Open UI and navigate to Agents tab
    await page.goto('/ui/');
    await page.waitForLoadState('networkidle');
    await Promise.all([
      page.waitForResponse(r => r.url().endsWith('/config') && r.ok()),
      page.click('[data-testid="tab-agents"]'),
    ]);

    // Helper to add a new agent via UI
    async function addAgentViaUi(name, { prompt, template }) {
      await page.fill('[data-test="new-name"]', name);
      // minimal required fields for validity
      await page.fill('input[placeholder="model.name"]', 'm1');
      // select at least one known model (test models are injected in test env)
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
      // Wait for /config to return with new agent present
      await page.waitForFunction(async (name) => {
        const res = await fetch('/config');
        if (!res.ok) return false;
        const json = await res.json();
        return json.agents && Object.prototype.hasOwnProperty.call(json.agents, name);
      }, name, { timeout: 15000 });
    }

    // Create two explicit-prompt agents
    await addAgentViaUi(agentA, { prompt: promptA });
    await addAgentViaUi(agentB, { prompt: promptB });

    // Create template-based agent
    await addAgentViaUi(agentTpl, { template: tplName });

    // Validate controller prompt resolution via /next-config?agent
    const resA = await request.get(`/next-config?agent=${encodeURIComponent(agentA)}`);
    expect(resA.ok()).toBeTruthy();
    const jsonA = await resA.json();
    expect(jsonA.prompt).toBe(promptA);

    const resB = await request.get(`/next-config?agent=${encodeURIComponent(agentB)}`);
    expect(resB.ok()).toBeTruthy();
    const jsonB = await resB.json();
    expect(jsonB.prompt).toBe(promptB);

    const resTpl = await request.get(`/next-config?agent=${encodeURIComponent(agentTpl)}`);
    expect(resTpl.ok()).toBeTruthy();
    const jsonTpl = await resTpl.json();
    expect(jsonTpl.prompt).toBe(tplText);

    // Cleanup: remove the three created agents via UI by clicking delete on their rows
    async function deleteAgentViaUi(name) {
      const row = page.locator('tbody tr', { hasText: name });
      await expect(row).toBeVisible({ timeout: 15000 });
      await row.locator('[data-test="delete"]').click();
      // Wait until /config no longer contains the agent
      await page.waitForFunction(async (name) => {
        const res = await fetch('/config');
        if (!res.ok) return false;
        const json = await res.json();
        return !json.agents || !Object.prototype.hasOwnProperty.call(json.agents, name);
      }, name, { timeout: 15000 });
    }

    await deleteAgentViaUi(agentA);
    await deleteAgentViaUi(agentB);
    await deleteAgentViaUi(agentTpl);
  });
});
