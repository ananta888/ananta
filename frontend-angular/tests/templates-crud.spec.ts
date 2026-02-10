import { test, expect } from '@playwright/test';
import { HUB_URL, login } from './utils';

test.describe('Templates CRUD', () => {
  async function getHubInfo(page: any) {
    return page.evaluate((defaultHubUrl: string) => {
      const token = localStorage.getItem('ananta.user.token');
      const raw = localStorage.getItem('ananta.agents.v1');
      let hubUrl = defaultHubUrl;
      if (raw) {
        try {
          const agents = JSON.parse(raw);
          const hub = agents.find((a: any) => a.role === 'hub');
          if (hub?.url) hubUrl = hub.url;
        } catch {}
      }
      if (!hubUrl || hubUrl === 'undefined') hubUrl = defaultHubUrl;
      return { hubUrl, token };
    }, HUB_URL);
  }

  test('create, update, delete template via API', async ({ page, request }) => {
    await login(page);
    await page.goto('/templates');
    await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();

    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const name = `E2E Template ${Date.now()}`;
    const description = 'E2E Beschreibung';
    const updatedDescription = 'E2E Beschreibung aktualisiert';

    const createRes = await request.post(`${hubUrl}/templates`, {
      headers,
      data: { name, description, prompt_template: 'Du bist {{agent_name}}. Aufgabe: {{task_title}}.' }
    });
    expect(createRes.ok()).toBeTruthy();
    const createBody = await createRes.json();
    const created = createBody?.data || createBody;
    expect(created?.id).toBeTruthy();

    const updateRes = await request.patch(`${hubUrl}/templates/${created.id}`, {
      headers,
      data: { description: updatedDescription }
    });
    expect(updateRes.ok()).toBeTruthy();

    const listRes = await request.get(`${hubUrl}/templates`, { headers });
    expect(listRes.ok()).toBeTruthy();
    const listBody = await listRes.json();
    const templates = Array.isArray(listBody) ? listBody : (listBody?.data || []);
    const found = templates.find((tpl: any) => tpl.id === created.id);
    expect(found).toBeTruthy();
    expect(found.description).toBe(updatedDescription);

    const delRes = await request.delete(`${hubUrl}/templates/${created.id}`, { headers });
    expect(delRes.ok()).toBeTruthy();

    const afterRes = await request.get(`${hubUrl}/templates`, { headers });
    expect(afterRes.ok()).toBeTruthy();
    const afterBody = await afterRes.json();
    const afterTemplates = Array.isArray(afterBody) ? afterBody : (afterBody?.data || []);
    expect(afterTemplates.find((tpl: any) => tpl.id === created.id)).toBeFalsy();
  });

  test('delete missing template returns not found', async ({ page, request }) => {
    await login(page);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const missingId = `tpl-missing-${Date.now()}`;
    const res = await request.delete(`${hubUrl}/templates/${missingId}`, { headers });
    expect(res.status()).toBe(404);
  });

  test('delete clears references when template is in use', async ({ page, request }) => {
    await login(page);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const tplName = `E2E InUse ${Date.now()}`;
    const tplRes = await request.post(`${hubUrl}/templates`, {
      headers,
      data: { name: tplName, description: 'in use', prompt_template: 'Du bist {{agent_name}}.' }
    });
    expect(tplRes.ok()).toBeTruthy();
    const tplResponse = await tplRes.json();
    const tpl = tplResponse?.data || tplResponse;

    const roleRes = await request.post(`${hubUrl}/teams/roles`, {
      headers,
      data: { name: `Role-${Date.now()}`, description: 'uses template', default_template_id: tpl.id }
    });
    expect(roleRes.ok()).toBeTruthy();
    const roleResponse = await roleRes.json();
    const role = roleResponse?.data || roleResponse;

    const del = await request.delete(`${hubUrl}/templates/${tpl.id}`, { headers });
    expect(del.ok()).toBeTruthy();

    const rolesRes = await request.get(`${hubUrl}/teams/roles`, { headers });
    expect(rolesRes.ok()).toBeTruthy();
    const rolesResponse = await rolesRes.json();
    const roles = Array.isArray(rolesResponse) ? rolesResponse : (rolesResponse?.data || []);
    const updatedRole = roles.find((r: any) => r.id === role.id);
    expect(updatedRole?.default_template_id).toBeFalsy();

    const templatesRes = await request.get(`${hubUrl}/templates`, { headers });
    expect(templatesRes.ok()).toBeTruthy();
    const templatesResponse = await templatesRes.json();
    const templates = Array.isArray(templatesResponse) ? templatesResponse : (templatesResponse?.data || []);
    expect(templates.find((t: any) => t.id === tpl.id)).toBeFalsy();
  });
});
