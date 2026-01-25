import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Templates CRUD', () => {
  test('create, edit, delete template', async ({ page }) => {
    await login(page);
    await page.goto('/templates');

    const name = `E2E Template ${Date.now()}`;
    const description = 'E2E Beschreibung';
    const updatedDescription = 'E2E Beschreibung aktualisiert';
    const prompt = 'Du bist {{agent_name}}. Aufgabe: {{task_title}}.';

    await page.getByPlaceholder('Name').fill(name);
    await page.getByPlaceholder('Beschreibung').fill(description);
    await page.getByLabel('Prompt Template').fill(prompt);
    await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();

    const card = page.locator('.grid.cols-2 .card', { has: page.getByText(name, { exact: true }) });
    await expect(card).toHaveCount(1);
    await expect(card).toContainText(description);

    await card.getByRole('button', { name: /Edit/i }).click();
    await page.getByPlaceholder('Beschreibung').fill(updatedDescription);
    await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();
    await expect(card).toContainText(updatedDescription);

    page.once('dialog', dialog => dialog.accept());
    await card.getByRole('button', { name: /L.schen/i }).click();
    await expect(card).toHaveCount(0);
  });

  test('delete failure shows error and keeps template', async ({ page }) => {
    await login(page);
    await page.goto('/templates');

    const name = `E2E Delete Fail ${Date.now()}`;
    const description = 'E2E Fehlerfall';
    const prompt = 'Du bist {{agent_name}}.';

    await page.getByPlaceholder('Name').fill(name);
    await page.getByPlaceholder('Beschreibung').fill(description);
    await page.getByLabel('Prompt Template').fill(prompt);
    await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();

    const card = page.locator('.grid.cols-2 .card', { has: page.getByText(name, { exact: true }) });
    await expect(card).toHaveCount(1);

    const deleteRoute = async (route: any) => {
      if (route.request().method() === 'DELETE') {
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ error: 'delete_failed' })
        });
        return;
      }
      await route.continue();
    };
    await page.route('**/templates/**', deleteRoute);

    page.once('dialog', dialog => dialog.accept());
    await card.getByRole('button', { name: /L.schen/i }).click();

    await expect(page.locator('.notification.error').first()).toBeVisible();
    await expect(card).toHaveCount(1);

    await page.unroute('**/templates/**', deleteRoute);
    page.once('dialog', dialog => dialog.accept());
    await card.getByRole('button', { name: /L.schen/i }).click();
    await expect(card).toHaveCount(0);
  });
});
