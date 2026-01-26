import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Templates CRUD', () => {
  async function getHubInfo(page: any) {
    return page.evaluate(() => {
      const token = localStorage.getItem('ananta.user.token');
      const raw = localStorage.getItem('ananta.agents.v1');
      let hubUrl = 'http://localhost:5000';
      if (raw) {
        try {
          const agents = JSON.parse(raw);
          const hub = agents.find((a: any) => a.role === 'hub');
          if (hub?.url) hubUrl = hub.url;
        } catch {}
      }
      return { hubUrl, token };
    });
  }

  test('create, edit, delete template', async ({ page, request }) => {
    await login(page);
    await page.goto('/templates');

    const name = `E2E Template ${Date.now()}`;
    const description = 'E2E Beschreibung';
    const updatedDescription = 'E2E Beschreibung aktualisiert';
    const prompt = 'Du bist {{agent_name}}. Aufgabe: {{task_title}}.';
    const { hubUrl, token } = await getHubInfo(page);

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
    const [del] = await Promise.all([
      page.waitForResponse(res => res.request().method() === 'DELETE' && res.url().includes('/templates/')),
      card.getByRole('button', { name: /L.schen/i }).click()
    ]);
    expect(del.ok()).toBeTruthy();

    await page.getByRole('button', { name: /Aktualisieren/i }).click();
    await expect(page.getByText(name, { exact: true })).toHaveCount(0);

    const listRes = await request.get(`${hubUrl}/templates`, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined
    });
    expect(listRes.ok()).toBeTruthy();
    const templates = await listRes.json();
    expect(templates.find((tpl: any) => tpl.name === name)).toBeFalsy();
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
    const tpl = await tplRes.json();

    const roleRes = await request.post(`${hubUrl}/teams/roles`, {
      headers,
      data: { name: `Role-${Date.now()}`, description: 'uses template', default_template_id: tpl.id }
    });
    expect(roleRes.ok()).toBeTruthy();
    const role = await roleRes.json();

    await page.goto('/templates');
    await page.getByRole('button', { name: /Aktualisieren/i }).click();
    const card = page.locator('.grid.cols-2 .card', { has: page.getByText(tplName, { exact: true }) });
    await expect(card).toHaveCount(1);

    page.once('dialog', dialog => dialog.accept());
    const [del] = await Promise.all([
      page.waitForResponse(res => res.request().method() === 'DELETE' && res.url().includes(`/templates/${tpl.id}`)),
      card.getByRole('button', { name: /L.schen/i }).click()
    ]);
    expect(del.ok()).toBeTruthy();

    const rolesRes = await request.get(`${hubUrl}/teams/roles`, { headers });
    expect(rolesRes.ok()).toBeTruthy();
    const roles = await rolesRes.json();
    const updatedRole = roles.find((r: any) => r.id === role.id);
    expect(updatedRole?.default_template_id).toBeFalsy();

    const templatesRes = await request.get(`${hubUrl}/templates`, { headers });
    expect(templatesRes.ok()).toBeTruthy();
    const templates = await templatesRes.json();
    expect(templates.find((t: any) => t.id === tpl.id)).toBeFalsy();
  });
});
