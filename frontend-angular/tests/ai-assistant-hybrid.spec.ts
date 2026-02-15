import { expect, test } from '@playwright/test';
import { login } from './utils';

test.describe('AI Assistant Hybrid Context', () => {
  test.describe.configure({ timeout: 120000 });
  test('renders context debug and citation preview in hybrid mode', async ({ page }) => {
    await login(page);
    await page.goto('/');

    let executeSeen = false;
    let executeFlag = false;
    let executeBackend: string | undefined;
    await page.route('**/api/sgpt/execute*', async route => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      executeSeen = true;
      const payload = route.request().postDataJSON() as any;
      executeFlag = Boolean(payload?.use_hybrid_context);
      executeBackend = payload?.backend;
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            output: 'Found probable timeout handling in `agent/routes/sgpt.py`.',
            errors: '',
            context: {
              policy_version: 'v1',
              chunk_count: 2,
              token_estimate: 140,
              strategy: { repository_map: 3, semantic_search: 1, agentic_search: 1 }
            }
          }
        })
      });
    });

    await page.route('**/api/sgpt/context*', async route => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            chunks: [
              { engine: 'repository_map', source: 'agent/routes/sgpt.py', score: 3.5 },
              { engine: 'semantic_search', source: 'docs/backend.md', score: 1.8 }
            ]
          }
        })
      });
    });

    await page.route('**/api/sgpt/source*', async route => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            source_path: 'agent/routes/sgpt.py',
            preview: 'def execute_sgpt():\n    ...',
            truncated: false,
            line_count: 2
          }
        })
      });
    });

    const header = page.locator('.ai-assistant-container .header');
    await header.click();

    await page.getByLabel(/Hybrid Context/i).check();
    await page.getByPlaceholder(/Ask me anything|Frage mich etwas/i).fill('where is timeout handling?');
    await page.getByRole('button', { name: /Send|Senden/i }).click();

    await expect.poll(() => executeSeen, { timeout: 30000 }).toBeTruthy();
    await expect.poll(() => executeFlag, { timeout: 30000 }).toBeTruthy();
    await expect.poll(() => executeBackend, { timeout: 30000 }).toBe('auto');
    await expect(page.locator('.assistant-msg').last()).toContainText(/Found probable timeout handling/i, { timeout: 30000 });

    const sourceRows = page.locator('.context-source-row');
    if ((await sourceRows.count()) > 0) {
      await sourceRows.first().getByRole('button', { name: 'Preview' }).click();
      await expect(page.locator('.source-preview', { hasText: /execute_sgpt/ })).toBeVisible();
    }
  });
});
