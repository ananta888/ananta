import { expect, test } from '@playwright/test';
import { login } from './utils';

test.describe('AI Assistant Settings Mutations', () => {
  test('requires explicit confirmation and sends confirmed tool calls', async ({ page }) => {
    await login(page);
    await page.evaluate(() => {
      localStorage.removeItem('ananta.ai-assistant.pending-plan');
      localStorage.removeItem('ananta.ai-assistant.history.v1');
    });

    let confirmPayload: any = null;
    await page.route('**/llm/generate', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const payload = route.request().postDataJSON() as any;
      if (payload?.confirm_tool_calls) {
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
        return;
      }

      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            response: 'Prepared config update.',
            requires_confirmation: true,
            tool_calls: [{ name: 'update_config', args: { key: 'http_timeout', value: 33 } }]
          }
        })
      });
    });

    const header = page.locator('.ai-assistant-container .header');
    await header.click();

    await page.getByPlaceholder(/Ask me anything/i).fill('set http timeout to 33');
    await page.getByRole('button', { name: /Send/i }).click();

    const runPlanBtn = page.getByRole('button', { name: /Run Plan/i }).last();
    await page.getByPlaceholder('Type RUN').fill('RUN');
    await runPlanBtn.click();

    await expect.poll(() => !!confirmPayload, { timeout: 10000 }).toBeTruthy();
    expect(confirmPayload.confirm_tool_calls).toBeTruthy();
    expect(Array.isArray(confirmPayload.tool_calls)).toBeTruthy();
    expect(confirmPayload.tool_calls[0].name).toBe('update_config');
  });
});
