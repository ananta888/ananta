import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Team Types and Roles', () => {
  test('create type, role, link role, map template, cleanup', async ({ page }) => {
    await login(page);

    const templateName = `E2E Template ${Date.now()}`;
    const roleName = `E2E Role ${Date.now()}`;
    const typeName = `E2E Type ${Date.now()}`;

    const templatesPromise1 = page.waitForResponse(res => res.url().includes('/templates') && res.request().method() === 'GET');
    await page.goto('/templates');
    await templatesPromise1;
    
    await page.getByPlaceholder('Name').fill(templateName);
    await page.getByPlaceholder('Beschreibung').fill('E2E Template Beschreibung');
    await page.getByLabel('Prompt Template').fill('Du bist {{agent_name}}.');
    
    const createTemplatePromise = page.waitForResponse(res => res.url().includes('/templates') && res.request().method() === 'POST');
    await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();
    const createTemplateResponse = await createTemplatePromise;
    expect(createTemplateResponse.ok()).toBeTruthy();

    const teamsPromise = page.waitForResponse(res => res.url().includes('/team-types') || res.url().includes('/roles'));
    await page.goto('/teams');
    await teamsPromise;
    await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
    const createTypeCard = page.locator('.card', { has: page.getByRole('heading', { name: /Team-Typ erstellen/i }) });
    await createTypeCard.getByPlaceholder(/Scrum Team/i).fill(typeName);
    await createTypeCard.getByPlaceholder(/Besonderheiten des Typs/i).fill('E2E Type Beschreibung');
    const createTypeButton = createTypeCard.getByRole('button', { name: /Typ Erstellen/i });
    await expect(createTypeButton).toBeEnabled({ timeout: 15000 });
    
    const createTypePromise = page.waitForResponse(res => res.url().includes('/teams/types') && res.request().method() === 'POST');
    await createTypeButton.click();
    const createTypeResponse = await createTypePromise;
    expect(createTypeResponse.ok()).toBeTruthy();
    const createdTypeCard = page.locator('.card', { has: page.getByText(typeName, { exact: true }) });

    await page.locator('.tab', { hasText: /^Rollen$/ }).click();
    const createRoleCard = page.locator('.card', { has: page.getByRole('heading', { name: /Rolle erstellen/i }) });
    await createRoleCard.getByPlaceholder(/Product Owner/i).fill(roleName);
    await createRoleCard.getByPlaceholder(/Aufgaben der Rolle/i).fill('E2E Role Beschreibung');
    await createRoleCard.getByLabel(/Standard Template/i).selectOption({ label: templateName });
    
    const createRolePromise = page.waitForResponse(res => res.url().includes('/teams/roles') && res.request().method() === 'POST');
    await createRoleCard.getByRole('button', { name: /Rolle Erstellen/i }).click();
    const createRoleResponse = await createRolePromise;
    expect(createRoleResponse.ok()).toBeTruthy();
    const roleCard = page.locator('.card', { has: page.getByText(roleName, { exact: true }) });

    await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
    const mappingTypeCard = page.locator('.card', { has: page.getByText(/^Scrum$/, { exact: true }) }).first();
    const roleCheckbox = mappingTypeCard.getByRole('checkbox', { name: /Flow Manager/i });
    const roleRow = roleCheckbox.locator('..');
    const roleSelect = roleRow.locator('select');
    if (!(await roleCheckbox.isChecked())) {
      await roleCheckbox.check();
      await expect(roleCheckbox).toBeChecked();
    }
    await expect(roleSelect).toBeEnabled();
    await roleSelect.selectOption({ label: templateName });

    if (await roleCheckbox.isChecked()) {
      await roleCheckbox.uncheck();
      await expect(roleCheckbox).not.toBeChecked();
    }

    await page.locator('.tab', { hasText: /^Rollen$/ }).click();
    page.once('dialog', dialog => dialog.accept());
    
    if (await roleCard.count()) {
      const deleteRolePromise = page.waitForResponse(res => res.url().includes('/teams/roles/') && res.request().method() === 'DELETE');
      await roleCard.getByRole('button', { name: /L.schen/i }).click();
      await deleteRolePromise;
      await expect(roleCard).toHaveCount(0);
    }

    await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
    if (await createdTypeCard.count()) {
      page.once('dialog', dialog => dialog.accept());
      const deleteTypePromise = page.waitForResponse(res => res.url().includes('/teams/types/') && res.request().method() === 'DELETE');
      await createdTypeCard.getByRole('button', { name: /L.schen/i }).click();
      await deleteTypePromise;
      await expect(createdTypeCard).toHaveCount(0);
    }

    const templatesPromise2 = page.waitForResponse(res => res.url().includes('/templates') && res.request().method() === 'GET');
    await page.goto('/templates');
    await templatesPromise2;
    const templateCard = page.locator('.grid.cols-2 .card', { has: page.getByText(templateName, { exact: true }) });
    page.once('dialog', dialog => dialog.accept());
    
    const deleteTemplatePromise = page.waitForResponse(res => res.url().includes('/templates/') && res.request().method() === 'DELETE');
    await templateCard.getByRole('button', { name: /L.schen/i }).click();
    await deleteTemplatePromise;
    
    await expect(templateCard).toHaveCount(0);
  });
});
