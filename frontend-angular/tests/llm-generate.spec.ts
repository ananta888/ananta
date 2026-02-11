import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('LLM Generate', () => {
  async function mockLlmGenerate(page: any, resolver: (body: string) => Promise<any> | any) {
    await page.route('**/llm/generate*', async (route: any) => {
      const method = route.request().method();
      if (method === 'OPTIONS') {
        await route.fulfill({
          status: 204,
          headers: {
            'access-control-allow-origin': '*',
            'access-control-allow-methods': 'POST,OPTIONS',
            'access-control-allow-headers': 'authorization,content-type'
          }
        });
        return;
      }

      const body = route.request().postData() || '';
      if (body.includes('"stream":true')) {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          headers: {
            'access-control-allow-origin': '*',
            'access-control-allow-methods': 'POST,OPTIONS',
            'access-control-allow-headers': 'authorization,content-type'
          },
          body: JSON.stringify({ error: 'stream_failed' })
        });
        return;
      }

      const payload = await resolver(body);
      await route.fulfill({
        status: payload?.status ?? 200,
        contentType: 'application/json',
        headers: {
          'access-control-allow-origin': '*',
          'access-control-allow-methods': 'POST,OPTIONS',
          'access-control-allow-headers': 'authorization,content-type'
        },
        body: JSON.stringify(payload?.body ?? payload)
      });
    });
  }

  async function openAssistant(page: any) {
    const container = page.locator('.ai-assistant-container');
    await container.locator('.header').click();
    await expect(container.locator('.content')).toBeVisible();
    return container;
  }

  test('shows assistant response from LLM', async ({ page }) => {
    await login(page);
    await mockLlmGenerate(page, async () => {
      return { response: 'Hallo vom LLM' };
    });

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    
    const container = await openAssistant(page);
    await container.getByPlaceholder('Frage mich etwas...').fill('Sag Hallo');
    
    await container.getByRole('button', { name: 'Senden' }).click();

    await expect(container.locator('.assistant-msg').last()).toContainText('Hallo vom LLM');
  });

  test('shows error toast on empty response', async ({ page }) => {
    await login(page);
    await mockLlmGenerate(page, async () => {
      return { response: '   ' };
    });

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    
    const container = await openAssistant(page);
    await container.getByPlaceholder('Frage mich etwas...').fill('Leere Antwort');
    
    await container.getByRole('button', { name: 'Senden' }).click();

    await expect(page.locator('.notification.error')).toContainText(/LLM/);
  });

  test('requires confirmation for tool calls', async ({ page }) => {
    await login(page);
    await mockLlmGenerate(page, async () => {
      return {
        response: 'Bitte bestaetigen.',
        requires_confirmation: true,
        tool_calls: [{ name: 'shell', args: { command: 'whoami' } }]
      };
    });

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    
    const container = await openAssistant(page);
    await container.getByPlaceholder('Frage mich etwas...').fill('Starte Tool');
    
    await container.getByRole('button', { name: 'Senden' }).click();

    const toolCard = container.locator('.assistant-msg').filter({ hasText: 'shell' }).last();
    await expect(toolCard.getByRole('button', { name: /Ausf/i })).toBeVisible();
    await expect(toolCard.getByRole('button', { name: /Abbre/i })).toBeVisible();
  });
});
