import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Teams', () => {
  test('create, edit, activate, delete team', async ({ page }) => {
    await login(page);
    await page.goto('/teams');

    const name = `E2E Team ${Date.now()}`;
    const description = 'E2E Team Beschreibung';
    const updatedDescription = 'E2E Team Beschreibung aktualisiert';

    const formCard = page.locator('.card', { has: page.getByRole('heading', { name: /Team konfigurieren/i }) });
    const typeSelect = formCard.getByLabel('Typ');
    const optionCount = await typeSelect.locator('option').count();
    let createdTypeName: string | null = null;
    if (optionCount <= 1) {
      createdTypeName = `E2E Type ${Date.now()}`;
      await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
      await page.getByLabel('Name').fill(createdTypeName);
      await page.getByLabel('Beschreibung').fill('E2E Type Beschreibung');
      await page.getByRole('button', { name: /Typ Erstellen/i }).click();
      const typeCard = page.locator('.card', { has: page.getByText(createdTypeName, { exact: true }) });
      await expect(typeCard).toHaveCount(1);
      await page.locator('.tab', { hasText: /^Teams$/ }).click();
      await expect(typeSelect.locator('option', { hasText: createdTypeName })).toHaveCount(1);
    }

    await formCard.getByLabel('Name').fill(name);
    if (createdTypeName) {
      await typeSelect.selectOption({ label: createdTypeName });
    } else if (optionCount > 1) {
      await typeSelect.selectOption({ index: 1 });
    }
    await formCard.getByLabel('Beschreibung').fill(description);
    await formCard.getByRole('button', { name: /Speichern/i }).click();
    await expect(page.locator('.notification.success', { hasText: /Team erstellt/i })).toBeVisible();

    const card = page.locator('.card', { has: page.getByText(name, { exact: true }) });
    await expect(card).toHaveCount(1);
    await expect(card).toContainText(description);

    await card.getByRole('button', { name: /Edit/i }).click();
    await formCard.getByLabel('Beschreibung').fill(updatedDescription);
    await formCard.getByRole('button', { name: /Speichern/i }).click();
    await expect(card).toContainText(updatedDescription);

    const activateButton = card.getByRole('button', { name: /Aktivieren/i });
    if (await activateButton.isVisible()) {
      await activateButton.click();
      await expect(card).toContainText('AKTIV');
    }

    page.once('dialog', dialog => dialog.accept());
    await card.getByRole('button', { name: /L.schen/i }).click();
    await expect(card).toHaveCount(0);

    if (createdTypeName) {
      await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
      const typeCard = page.locator('.card', { has: page.getByText(createdTypeName, { exact: true }) });
      page.once('dialog', dialog => dialog.accept());
      await typeCard.getByRole('button', { name: /L.schen/i }).click();
      await expect(typeCard).toHaveCount(0);
    }
  });
});
