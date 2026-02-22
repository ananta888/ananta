import { expect, test } from '@playwright/test';
import { login } from './utils';

test.describe('AI Assistant Settings Mutations', () => {
  test('requires explicit confirmation and sends confirmed tool calls', async ({ page }) => {
    await login(page);
    await page.evaluate(() => {
      localStorage.removeItem('ananta.ai-assistant.pending-plan');
      localStorage.removeItem('ananta.ai-assistant.history.v1');
      localStorage.setItem('ananta.ai-assistant.pending-plan', JSON.stringify({
        pendingPrompt: 'set http timeout to 33',
        toolCalls: [{ name: 'update_config', args: { key: 'http_timeout', value: 33 } }],
        createdAt: Date.now()
      }));
    });
    await page.reload();

    let confirmPayload: any = null;
    await page.route('**/llm/generate', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const payload = route.request().postDataJSON() as any;
      confirmPayload = payload;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            response: 'http_timeout updated',
            tool_results: [{ tool: 'update_config', success: true, output: 'ok', error: null }]
          }
        })
      });
    });

    const header = page.locator('.ai-assistant-container .header');
    await header.click();
    const runPlanBtn = page.getByRole('button', { name: /Run Plan/i }).first();
    await expect(page.getByPlaceholder('Type RUN')).toBeVisible();
    await page.getByPlaceholder('Type RUN').fill('RUN');
    await runPlanBtn.click();

    await expect.poll(() => !!confirmPayload, { timeout: 10000 }).toBeTruthy();
    expect(confirmPayload.confirm_tool_calls).toBeTruthy();
    expect(Array.isArray(confirmPayload.tool_calls)).toBeTruthy();
    expect(confirmPayload.tool_calls[0].name).toBe('update_config');
  });

  test('sends settings context for auto-planner mutation tool call', async ({ page }) => {
    await login(page);
    await page.evaluate(() => {
      localStorage.removeItem('ananta.ai-assistant.pending-plan');
      localStorage.removeItem('ananta.ai-assistant.history.v1');
      localStorage.setItem('ananta.ai-assistant.pending-plan', JSON.stringify({
        pendingPrompt: 'enable auto planner',
        toolCalls: [{ name: 'configure_auto_planner', args: { enabled: true, max_subtasks_per_goal: 8 } }],
        createdAt: Date.now()
      }));
    });

    await page.route('**/assistant/read-model', async route => {
      if (route.request().method() !== 'GET') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            config: { effective: { default_provider: 'lmstudio', quality_gates: { enabled: true } } },
            teams: { count: 1, items: [] },
            roles: { count: 0, items: [] },
            templates: { count: 0, items: [] },
            agents: { count: 1, items: [{ name: 'hub', role: 'hub', url: 'http://localhost:5000' }] },
            settings: {
              summary: { llm: { default_provider: 'lmstudio' } },
              editable_inventory: [{ key: 'auto_planner', path: 'tasks.auto_planner', type: 'object' }]
            },
            automation: { auto_planner: { enabled: false } }
          }
        })
      });
    });

    let confirmPayload: any = null;
    await page.route('**/llm/generate', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const payload = route.request().postDataJSON() as any;
      confirmPayload = payload;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            response: 'auto planner updated',
            tool_results: [{ tool: 'configure_auto_planner', success: true, output: 'ok', error: null }]
          }
        })
      });
    });

    await page.reload();
    const header = page.locator('.ai-assistant-container .header');
    await header.click();
    await page.getByPlaceholder('Type RUN').fill('RUN');
    await page.getByRole('button', { name: /Run Plan/i }).first().click();

    await expect.poll(() => !!confirmPayload, { timeout: 10000 }).toBeTruthy();
    expect(confirmPayload.confirm_tool_calls).toBeTruthy();
    expect(confirmPayload.context?.settings_summary?.llm?.default_provider).toBe('lmstudio');
    expect(Array.isArray(confirmPayload.context?.editable_settings)).toBeTruthy();
    expect(confirmPayload.tool_calls[0].name).toBe('configure_auto_planner');
  });
});
