import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Templates AI', () => {
  test('shows error toast when LLM fails', async ({ page }) => {
    await login(page);
    await page.goto('/templates');
    await page.route('**/llm/generate', async route => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 502,
        contentType: 'application/json',
        body: JSON.stringify({ error: 'llm_failed' })
      });
    });

    await page.getByPlaceholder(/Beschreibe das Template/i).fill(
      'Ein Template fuer API Fehlerbehandlung mit klaren Schritten und Beispielen.'
    );
    await page.getByRole('button', { name: /Entwurf/i }).click();

    await expect(page.locator('.notification.error', { hasText: 'KI-Generierung fehlgeschlagen' })).toBeVisible();
  });

  test('generates template draft when LLM responds', async ({ page }) => {
    await login(page);
    await page.goto('/templates');
    await page.route('**/llm/generate', async route => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const draft = {
        name: 'API Fehlerbehandlung',
        description: 'Schritte zur Diagnose, Korrektur und Verifikation.',
        prompt_template: 'Du bist {{agent_name}}. Folge den Schritten fuer {{task_title}}.'
      };
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ response: JSON.stringify(draft) })
      });
    });

    await page.getByPlaceholder(/Beschreibe das Template/i).fill(
      'Ein Template fuer API Fehlerbehandlung mit klaren Schritten und Beispielen.'
    );
    await page.getByRole('button', { name: /Entwurf/i }).click();

    const nameInput = page.getByPlaceholder('Name');
    const descInput = page.getByPlaceholder('Beschreibung');
    const promptArea = page.getByLabel('Prompt Template');

    await expect(nameInput).toHaveValue(/API Fehlerbehandlung/);
    await expect(descInput).toHaveValue(/Schritte zur Diagnose/);
    await expect(promptArea).toHaveValue(/{{agent_name}}/);
  });
});
