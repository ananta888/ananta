import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Team Types and Roles', () => {
  test('create type, role, link role, map template, cleanup', async ({ page }) => {
    await login(page);

    const templateName = `E2E Template ${Date.now()}`;
    const roleName = `E2E Role ${Date.now()}`;
    const typeName = `E2E Type ${Date.now()}`;

    await page.goto('/templates');
    await page.getByPlaceholder('Name').fill(templateName);
    await page.getByPlaceholder('Beschreibung').fill('E2E Template Beschreibung');
    await page.getByLabel('Prompt Template').fill('Du bist {{agent_name}}.');
    await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();
    await expect(page.locator('.grid.cols-2 .card', { has: page.getByText(templateName, { exact: true }) })).toHaveCount(1);

    await page.goto('/teams');
    await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
    await page.getByLabel('Name').fill(typeName);
    await page.getByLabel('Beschreibung').fill('E2E Type Beschreibung');
    await page.getByRole('button', { name: /Typ Erstellen/i }).click();

    const typeCard = page.locator('.card', { has: page.getByText(typeName, { exact: true }) });
    await expect(typeCard).toHaveCount(1);

    await page.locator('.tab', { hasText: /^Rollen$/ }).click();
    await page.getByLabel('Name').fill(roleName);
    await page.getByLabel('Beschreibung').fill('E2E Role Beschreibung');
    await page.getByLabel(/Standard Template/i).selectOption({ label: templateName });
    await page.getByRole('button', { name: /Rolle Erstellen/i }).click();
    const roleCard = page.locator('.card', { has: page.getByText(roleName, { exact: true }) });
    await expect(roleCard).toHaveCount(1);

    await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
    const roleCheckbox = typeCard.getByRole('checkbox', { name: roleName });
    await roleCheckbox.check();
    await expect(roleCheckbox).toBeChecked();

    const roleRow = roleCheckbox.locator('..');
    const roleSelect = roleRow.locator('select');
    await expect(roleSelect).toBeEnabled();
    await roleSelect.selectOption({ label: templateName });

    await roleCheckbox.uncheck();
    await expect(roleCheckbox).not.toBeChecked();
    await expect(roleSelect).toBeDisabled();

    await page.locator('.tab', { hasText: /^Rollen$/ }).click();
    page.once('dialog', dialog => dialog.accept());
    await roleCard.getByRole('button', { name: /L.schen/i }).click();
    await expect(roleCard).toHaveCount(0);

    await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
    page.once('dialog', dialog => dialog.accept());
    await typeCard.getByRole('button', { name: /L.schen/i }).click();
    await expect(typeCard).toHaveCount(0);

    await page.goto('/templates');
    const templateCard = page.locator('.grid.cols-2 .card', { has: page.getByText(templateName, { exact: true }) });
    page.once('dialog', dialog => dialog.accept());
    await templateCard.getByRole('button', { name: /L.schen/i }).click();
    await expect(templateCard).toHaveCount(0);
  });
});
