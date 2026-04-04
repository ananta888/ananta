import { test, expect } from '@playwright/test';
import { loginFast } from './utils';

test.describe('Team Types and Roles', () => {
  test('create type, role, link role, map template, cleanup', async ({ page }) => {
    const HUB_URL = process.env.E2E_HUB_URL || 'http://localhost:5500';
    const extractId = (payload: any): string | undefined =>
      payload?.id || payload?.data?.id || payload?.data?.data?.id;

    await loginFast(page, page.request);

    const templateName = `E2E Template ${Date.now()}`;
    const roleName = `E2E Role ${Date.now()}`;
    const typeName = `E2E Type ${Date.now()}`;

    const templatesPromise1 = page.waitForResponse(res => res.url().includes('/templates') && res.request().method() === 'GET');
    await page.goto('/templates');
    await templatesPromise1;

    const templateNameInput = page.getByPlaceholder('Name');
    await expect(templateNameInput).toBeEnabled({ timeout: 20000 });
    await templateNameInput.fill(templateName);
    await page.getByPlaceholder('Beschreibung').fill('E2E Template Beschreibung');
    await page.getByLabel('Prompt Template').fill('Du bist {{agent_name}}.');
    
    const createTemplatePromise = page.waitForResponse(res => res.url().includes('/templates') && res.request().method() === 'POST');
    await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();
    const createTemplateResponse = await createTemplatePromise;
    expect(createTemplateResponse.ok()).toBeTruthy();
    const createdTemplate = await createTemplateResponse.json().catch(() => ({}));
    const templateId = extractId(createdTemplate);
    expect(templateId).toBeTruthy();

    const teamsPromise = page.waitForResponse(res => res.url().includes('/teams/types') || res.url().includes('/teams/roles'));
    await page.goto('/teams');
    await teamsPromise;
    await page.locator('.tab', { hasText: /^Advanced$/ }).click();
    await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
    const createTypeCard = page.locator('.card', { has: page.getByRole('heading', { name: /Team-Typ erstellen/i }) });
    await createTypeCard.getByLabel('Name').fill(typeName);
    await createTypeCard.getByLabel('Beschreibung').fill('E2E Type Beschreibung');
    const createTypeButton = createTypeCard.getByRole('button', { name: /Typ erstellen/i });
    await expect(createTypeButton).toBeEnabled({ timeout: 15000 });
    
    const createTypePromise = page.waitForResponse(res => res.url().includes('/teams/types') && res.request().method() === 'POST');
    await createTypeButton.click();
    const createTypeResponse = await createTypePromise;
    expect(createTypeResponse.ok()).toBeTruthy();
    const createdType = await createTypeResponse.json().catch(() => ({}));
    const typeId = extractId(createdType);
    expect(typeId).toBeTruthy();
    const createdTypeCard = page.locator('.card', { has: page.getByText(typeName, { exact: true }) });

    await page.locator('.tab', { hasText: /^Rollen$/ }).click();
    const createRoleCard = page.locator('.card', { has: page.getByRole('heading', { name: /Rolle erstellen/i }) });
    await createRoleCard.getByLabel('Name').fill(roleName);
    await createRoleCard.getByLabel('Beschreibung').fill('E2E Role Beschreibung');
    await createRoleCard.getByLabel(/Standard Template/i).selectOption({ label: templateName });
    
    const createRolePromise = page.waitForResponse(res => res.url().includes('/teams/roles') && res.request().method() === 'POST');
    await createRoleCard.getByRole('button', { name: /Rolle erstellen/i }).click();
    const createRoleResponse = await createRolePromise;
    expect(createRoleResponse.ok()).toBeTruthy();
    const createdRole = await createRoleResponse.json().catch(() => ({}));
    const roleId = extractId(createdRole);
    expect(roleId).toBeTruthy();
    const roleCard = page.locator('.card', { has: page.getByText(roleName, { exact: true }) });

    const authToken = await page.evaluate(() => localStorage.getItem('ananta.user.token') || '');
    expect(authToken).toBeTruthy();

    const linkRoleApi = await page.request.post(`${HUB_URL}/teams/types/${typeId}/roles`, {
      headers: { Authorization: `Bearer ${authToken}` },
      data: { role_id: roleId }
    });
    expect(linkRoleApi.ok()).toBeTruthy();

    const mapTemplateApi = await page.request.patch(`${HUB_URL}/teams/types/${typeId}/roles/${roleId}`, {
      headers: { Authorization: `Bearer ${authToken}` },
      data: { template_id: templateId }
    });
    expect(mapTemplateApi.ok()).toBeTruthy();

    const unlinkRoleApi = await page.request.delete(`${HUB_URL}/teams/types/${typeId}/roles/${roleId}`, {
      headers: { Authorization: `Bearer ${authToken}` }
    });
    expect(unlinkRoleApi.ok()).toBeTruthy();

    await page.locator('.tab', { hasText: /^Advanced$/ }).click();
    await page.locator('.tab', { hasText: /^Rollen$/ }).click();
    const deleteRoleApi = await page.request.delete(`${HUB_URL}/teams/roles/${roleId}`, {
      headers: { Authorization: `Bearer ${authToken}` }
    });
    expect(deleteRoleApi.ok()).toBeTruthy();

    await page.locator('.tab', { hasText: /^Team-Typen$/ }).click();
    const deleteTypeApi = await page.request.delete(`${HUB_URL}/teams/types/${typeId}`, {
      headers: { Authorization: `Bearer ${authToken}` }
    });
    expect(deleteTypeApi.ok()).toBeTruthy();

    const templatesPromise2 = page.waitForResponse(res => res.url().includes('/templates') && res.request().method() === 'GET');
    await page.goto('/templates');
    await templatesPromise2;
    const deleteTemplateApi = await page.request.delete(`${HUB_URL}/templates/${templateId}`, {
      headers: { Authorization: `Bearer ${authToken}` }
    });
    expect(deleteTemplateApi.ok()).toBeTruthy();
  });
});
