import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('LLM Generate', () => {
  async function openAssistant(page: any) {
    const container = page.locator('.ai-assistant-container');
    await container.locator('.header').click();
    await expect(container.locator('.content')).toBeVisible();
    return container;
  }

  test('shows assistant response from LLM', async ({ page }) => {
    await login(page);
    await page.route('**/llm/generate*', async route => {
      const body = route.request().postData() || '';
      if (body.includes('"stream":true')) {
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'stream_failed' }) });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ response: 'Hallo vom LLM' })
      });
    });

    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');
    
    const container = await openAssistant(page);
    await container.getByPlaceholder('Frage mich etwas...').fill('Sag Hallo');
    
    const llmPromise = page.waitForResponse(res => res.url().includes('/llm/generate'));
    await container.getByRole('button', { name: 'Senden' }).click();
    await llmPromise;

    await expect(container.locator('.assistant-msg').last()).toContainText('Hallo vom LLM');
  });

  test('shows error toast on empty response', async ({ page }) => {
    await login(page);
    await page.route('**/llm/generate*', async route => {
      const body = route.request().postData() || '';
      if (body.includes('"stream":true')) {
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'stream_failed' }) });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ response: '   ' })
      });
    });

    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');
    
    const container = await openAssistant(page);
    await container.getByPlaceholder('Frage mich etwas...').fill('Leere Antwort');
    
    const llmPromise = page.waitForResponse(res => res.url().includes('/llm/generate'));
    await container.getByRole('button', { name: 'Senden' }).click();
    await llmPromise;

    await expect(page.locator('.notification.error')).toContainText(/LLM/);
  });

  test('requires confirmation for tool calls', async ({ page }) => {
    await login(page);
    await page.route('**/llm/generate*', async route => {
      const body = route.request().postData() || '';
      if (body.includes('"stream":true')) {
        await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'stream_failed' }) });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          response: 'Bitte bestaetigen.',
          requires_confirmation: true,
          tool_calls: [{ name: 'shell', args: { command: 'whoami' } }]
        })
      });
    });

    await page.goto('/dashboard');
    await page.waitForLoadState('networkidle');
    
    const container = await openAssistant(page);
    await container.getByPlaceholder('Frage mich etwas...').fill('Starte Tool');
    
    const llmPromise = page.waitForResponse(res => res.url().includes('/llm/generate'));
    await container.getByRole('button', { name: 'Senden' }).click();
    await llmPromise;

    const toolCard = container.locator('.assistant-msg').filter({ hasText: 'shell' }).last();
    await expect(toolCard.getByRole('button', { name: /Ausf/i })).toBeVisible();
    await expect(toolCard.getByRole('button', { name: /Abbre/i })).toBeVisible();
  });
});
