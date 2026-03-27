import { expect, test } from '@playwright/test';
import { loginFast } from './utils';
import { ensureAssistantExpanded, hasAssistantDock } from './helpers/assistant-dock';

test.describe('AI Assistant OpenCode Backend', () => {
  test('sends hybrid execute request with backend opencode', async ({ page, request }) => {
    test.setTimeout(120_000);
    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    expect(await hasAssistantDock(page)).toBeTruthy();
    expect(await ensureAssistantExpanded(page)).toBeTruthy();

    await page.route('**/api/sgpt/backends*', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            supported_backends: {
              sgpt: { available: true },
              opencode: { available: true }
            }
          }
        })
      });
    });

    let executeBackend = '';
    let executeSeen = false;
    await page.route('**/api/sgpt/execute*', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const body = route.request().postDataJSON() as any;
      executeSeen = true;
      executeBackend = String(body?.backend || '');
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            output: 'ok from opencode',
            backend: 'opencode',
            context: { chunk_count: 1, token_estimate: 42 }
          }
        })
      });
    });

    await page.getByLabel(/CLI Backend/i).selectOption('opencode');
    await page.getByLabel(/Hybrid Context/i).check();
    await page.getByPlaceholder(/Ask me anything|Frage mich etwas/i).fill('run with opencode');
    await page.getByRole('button', { name: /Send|Senden/i }).click();

    await expect.poll(() => executeSeen, { timeout: 15000 }).toBeTruthy();
    await expect.poll(() => executeBackend, { timeout: 15000 }).toBe('opencode');
    await expect(page.locator('.assistant-msg').last()).toContainText(/ok from opencode/i, { timeout: 15000 });
  });
});
