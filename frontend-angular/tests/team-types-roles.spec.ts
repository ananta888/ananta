import { test, expect } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, HUB_URL, getAccessToken, loginFast } from './utils';

test.describe('Team Types and Roles', () => {
  test('create type, role, link role, map template, cleanup', async ({ page }) => {
    const extractId = (payload: any): string | undefined =>
      payload?.id || payload?.data?.id || payload?.data?.data?.id;

    await loginFast(page, page.request);
    const authToken = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);
    const authHeaders = { Authorization: `Bearer ${authToken}` };

    const templateName = `E2E Template ${Date.now()}`;
    const roleName = `E2E Role ${Date.now()}`;
    const typeName = `E2E Type ${Date.now()}`;

    const createTemplateResponse = await page.request.post(`${HUB_URL}/templates`, {
      headers: authHeaders,
      data: {
        name: templateName,
        description: 'E2E Template Beschreibung',
        prompt_template: 'Du bist {{agent_name}}.',
      }
    });
    expect(createTemplateResponse.ok()).toBeTruthy();
    const createdTemplate = await createTemplateResponse.json().catch(() => ({}));
    const templateId = extractId(createdTemplate);
    expect(templateId).toBeTruthy();

    const createTypeResponse = await page.request.post(`${HUB_URL}/teams/types`, {
      headers: authHeaders,
      data: {
        name: typeName,
        description: 'E2E Type Beschreibung',
      }
    });
    expect(createTypeResponse.ok()).toBeTruthy();
    const createdType = await createTypeResponse.json().catch(() => ({}));
    const typeId = extractId(createdType);
    expect(typeId).toBeTruthy();

    const createRoleResponse = await page.request.post(`${HUB_URL}/teams/roles`, {
      headers: authHeaders,
      data: {
        name: roleName,
        description: 'E2E Role Beschreibung',
        default_template_id: templateId,
      }
    });
    expect(createRoleResponse.ok()).toBeTruthy();
    const createdRole = await createRoleResponse.json().catch(() => ({}));
    const roleId = extractId(createdRole);
    expect(roleId).toBeTruthy();

    const teamsPromise = page.waitForResponse(res => res.url().includes('/teams/types') || res.url().includes('/teams/roles') || res.url().includes('/teams?'));
    await page.goto('/teams');
    await teamsPromise;
    await expect(page.getByRole('heading', { name: /Teams/i })).toBeVisible();

    const linkRoleApi = await page.request.post(`${HUB_URL}/teams/types/${typeId}/roles`, {
      headers: authHeaders,
      data: { role_id: roleId }
    });
    expect(linkRoleApi.ok()).toBeTruthy();

    const mapTemplateApi = await page.request.patch(`${HUB_URL}/teams/types/${typeId}/roles/${roleId}`, {
      headers: authHeaders,
      data: { template_id: templateId }
    });
    expect(mapTemplateApi.ok()).toBeTruthy();

    const unlinkRoleApi = await page.request.delete(`${HUB_URL}/teams/types/${typeId}/roles/${roleId}`, {
      headers: authHeaders
    });
    expect(unlinkRoleApi.ok()).toBeTruthy();

    const deleteRoleApi = await page.request.delete(`${HUB_URL}/teams/roles/${roleId}`, {
      headers: authHeaders
    });
    expect(deleteRoleApi.ok()).toBeTruthy();

    const deleteTypeApi = await page.request.delete(`${HUB_URL}/teams/types/${typeId}`, {
      headers: authHeaders
    });
    expect(deleteTypeApi.ok()).toBeTruthy();

    const deleteTemplateApi = await page.request.delete(`${HUB_URL}/templates/${templateId}`, {
      headers: authHeaders
    });
    expect(deleteTemplateApi.ok()).toBeTruthy();
  });
});
