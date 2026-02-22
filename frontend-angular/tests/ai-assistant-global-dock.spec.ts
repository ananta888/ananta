import { expect, test } from '@playwright/test';
import { login } from './utils';

test.describe('AI Assistant Global Dock', () => {
  async function ensureAssistantExpanded(page: any) {
    const container = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    const header = page.locator('[data-testid="assistant-dock-header"], .ai-assistant-container .header').first();
    await expect(container).toBeVisible();
    const state = await container.getAttribute('data-state');
    if (state === 'minimized') {
      await header.click();
    }
    await expect(page.locator('[data-testid="assistant-dock-input"], input[placeholder=\"Ask me anything...\"]').first()).toBeVisible();
  }

  test('is available across main routes and can interact on each page', async ({ page }) => {
    await login(page);
    const container = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    await page.evaluate(() => {
      localStorage.removeItem('ananta.ai-assistant.pending-plan');
      localStorage.removeItem('ananta.ai-assistant.history.v1');
    });
    await page.reload();

    await page.route('**/llm/generate', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { response: 'ok', tool_calls: [] } }),
      });
    });

    await expect(container).toBeVisible();
    await ensureAssistantExpanded(page);

    await page.getByPlaceholder(/Ask me anything/i).fill('hello dashboard');
    await page.getByRole('button', { name: /Send/i }).click();
    await expect(page.locator('.msg-bubble.user-msg', { hasText: 'hello dashboard' })).toBeVisible();

    await page.goto('/settings');
    await ensureAssistantExpanded(page);
    await page.getByPlaceholder(/Ask me anything/i).fill('hello settings');
    await page.getByRole('button', { name: /Send/i }).click();
    await expect(page.locator('.msg-bubble.user-msg', { hasText: 'hello settings' })).toBeVisible();

    await page.goto('/teams');
    await ensureAssistantExpanded(page);
    await page.getByPlaceholder(/Ask me anything/i).fill('hello teams');
    await page.getByRole('button', { name: /Send/i }).click();
    await expect(page.locator('.msg-bubble.user-msg', { hasText: 'hello teams' })).toBeVisible();
  });

  test('uses fullscreen overlay behavior on mobile when expanded', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await login(page);
    const container = page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
    const header = page.locator('[data-testid="assistant-dock-header"], .ai-assistant-container .header').first();
    await expect(container).toBeVisible();
    await header.click();
    await expect(container).not.toHaveClass(/minimized/);
  });

  test('sends template summary in assistant context for llm requests', async ({ page }) => {
    test.setTimeout(120_000);
    await login(page);
    let capturedContext: any = null;

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
            config: { effective: { default_provider: 'lmstudio' } },
            teams: { count: 1, items: [{ id: 'team-1', name: 'Scrum Team' }] },
            agents: { count: 1, items: [{ name: 'hub', role: 'hub', url: 'http://localhost:5000' }] },
            templates: {
              count: 2,
              items: [
                { id: 'tpl-1', name: 'Scrum - Product Owner', description: 'Backlog-Pflege und Priorisierung.' },
                { id: 'tpl-2', name: 'Scrum - Developer', description: 'Implementierung und Qualitaetssicherung.' }
              ]
            },
            settings: {
              summary: {
                llm: { default_provider: 'lmstudio', default_model: 'qwen2.5-coder' },
                system: { http_timeout: 30 }
              },
              editable_inventory: [
                { key: 'default_provider', path: 'config.default_provider', type: 'enum', endpoint: 'POST /config' },
                { key: 'http_timeout', path: 'config.http_timeout', type: 'integer', endpoint: 'POST /config' }
              ]
            },
            automation: {
              autopilot: { running: false },
              auto_planner: { enabled: true },
              triggers: { auto_start_planner: true }
            }
          }
        })
      });
    });

    await page.route('**/llm/generate*', async route => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const body = route.request().postDataJSON() as any;
      const context = body?.context || {};
      capturedContext = context;
      expect(Array.isArray(context.templates_summary)).toBeTruthy();
      expect(context.templates_summary).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ name: 'Scrum - Product Owner' }),
          expect.objectContaining({ name: 'Scrum - Developer' }),
        ])
      );
      expect(context.settings_summary?.llm?.default_provider).toBe('lmstudio');
      expect(context.editable_settings).toEqual(
        expect.arrayContaining([
          expect.objectContaining({ key: 'default_provider' }),
          expect.objectContaining({ key: 'http_timeout' }),
        ])
      );
      expect(context.automation_summary?.autopilot?.running).toBe(false);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ response: 'ok' }),
      });
    });

    await page.goto('/dashboard');
    await ensureAssistantExpanded(page);
    await page.getByPlaceholder(/Ask me anything|Frage mich etwas/i).fill('ergaenze alle weiteren scrum templates');
    await page.getByRole('button', { name: /Send|Senden/i }).click();
    await expect.poll(
      () => Array.isArray(capturedContext?.templates_summary) ? capturedContext.templates_summary.length : 0,
      { timeout: 30_000 }
    ).toBeGreaterThan(0);
  });
});
