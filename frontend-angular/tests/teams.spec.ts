import { test, expect } from '@playwright/test';
import { HUB_URL, loginFast, openTeamsAdminStudio, requestWithRetry } from './utils';

test.describe('Teams CRUD', () => {
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

  test('create, update, activate, delete team via API', async ({ page, request }) => {
    await loginFast(page, request);
    await page.goto('/teams');
    await expect(page.getByText(/Blueprint-first Teams/i)).toBeVisible();

    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const name = `E2E Team ${Date.now()}`;
    const updatedDescription = 'E2E Team Beschreibung aktualisiert';

    const createRes = await requestWithRetry(request, 'post', `${hubUrl}/teams`, {
      headers,
      data: { name, description: 'E2E Team Beschreibung', members: [] }
    });
    expect(createRes.status()).toBe(201);
    const createBody = await createRes.json();
    const created = createBody?.data || createBody;
    expect(created?.id).toBeTruthy();

    const patchRes = await requestWithRetry(request, 'patch', `${hubUrl}/teams/${created.id}`, {
      headers,
      data: { description: updatedDescription }
    });
    expect(patchRes.ok()).toBeTruthy();

    const activateRes = await requestWithRetry(request, 'post', `${hubUrl}/teams/${created.id}/activate`, {
      headers,
      data: {}
    });
    expect(activateRes.ok()).toBeTruthy();

    const listRes = await requestWithRetry(request, 'get', `${hubUrl}/teams`, { headers });
    expect(listRes.ok()).toBeTruthy();
    const listBody = await listRes.json();
    const teams = Array.isArray(listBody) ? listBody : (listBody?.data || []);
    const found = teams.find((team: any) => team.id === created.id);
    expect(found).toBeTruthy();
    expect(found.description).toBe(updatedDescription);
    expect(found.is_active).toBeTruthy();

    const delRes = await requestWithRetry(request, 'delete', `${hubUrl}/teams/${created.id}`, { headers });
    expect(delRes.ok()).toBeTruthy();

    const afterRes = await requestWithRetry(request, 'get', `${hubUrl}/teams`, { headers });
    expect(afterRes.ok()).toBeTruthy();
    const afterBody = await afterRes.json();
    const afterTeams = Array.isArray(afterBody) ? afterBody : (afterBody?.data || []);
    expect(afterTeams.find((team: any) => team.id === created.id)).toBeFalsy();
  });

  test('blueprint-first page shows blueprint and advanced flows', async ({ page, request }) => {
    await loginFast(page, request);
    await page.goto('/teams');

    await expect(page.getByText(/Blueprint-first Teams/i)).toBeVisible();
    await expect(page.locator('.teams-hero-actions').getByRole('button', { name: /^Blueprints$/i })).toBeVisible();
    await expect(page.locator('.teams-hero-actions').getByRole('button', { name: /^Team erstellen$/i })).toBeVisible();

    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const scrumBlueprintId = await expect.poll(async () => {
      try {
        const blueprintsRes = await requestWithRetry(request, 'get', `${hubUrl}/teams/blueprints`, {
          headers,
          timeoutMs: 20_000,
        });
        if (!blueprintsRes.ok()) return '';
        const blueprintsBody = await blueprintsRes.json();
        const blueprints = Array.isArray(blueprintsBody) ? blueprintsBody : (blueprintsBody?.data || []);
        return blueprints.find((blueprint: any) => blueprint.name === 'Scrum')?.id || '';
      } catch {
        return '';
      }
    }, { timeout: 30_000 }).not.toBe('');
    const scrumBlueprint = { id: scrumBlueprintId, name: 'Scrum' };

    await page.getByRole('button', { name: /^Teams aus Blueprint$/i }).click();
    const blueprintSelect = page.getByLabel('Blueprint').first();
    await expect.poll(async () => {
      return blueprintSelect.locator('option').count();
    }, { timeout: 20_000 }).toBeGreaterThanOrEqual(1);
    if (await blueprintSelect.locator(`option[value="${scrumBlueprint.id}"]`).count()) {
      await blueprintSelect.selectOption(String(scrumBlueprint.id));
    } else if (await blueprintSelect.locator('option', { hasText: String(scrumBlueprint.name) }).count()) {
      await blueprintSelect.selectOption({ label: String(scrumBlueprint.name) });
    }
    await page.getByLabel('Teamname').fill(`UI Blueprint Team ${Date.now()}`);
    await expect(page.getByRole('button', { name: /^Team erstellen$/i }).first()).toBeVisible();

    await page.getByRole('button', { name: /Admin-\/Studio-Modus/i }).click();
    await page.getByRole('button', { name: /^Advanced$/i }).click();
    await expect(page.getByRole('heading', { name: /^Advanced-Modus$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Team-Typen$/i })).toBeVisible();
  });

  test('delete missing team returns not found', async ({ page, request }) => {
    await loginFast(page, request);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const missingId = `team-missing-${Date.now()}`;
    const res = await requestWithRetry(request, 'delete', `${hubUrl}/teams/${missingId}`, {
      headers,
      attempts: 1,
    });
    expect(res.status()).toBe(404);
  });

  test('blueprint editor shows validation errors and can instantiate a custom blueprint', async ({ page, request }) => {
    await loginFast(page, request);
    await openTeamsAdminStudio(page);

    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
    const blueprintName = `UI Blueprint ${Date.now()}`;
    const teamName = `${blueprintName} Team`;
    let createdBlueprintId: string | undefined;
    let createdTeamId: string | undefined;
    const blueprintList = page.locator('.teams-list-panel');
    const blueprintEditor = page.locator('.teams-editor-panel');
    const instantiatePanel = page.locator('.card.card-success');

    try {
      await page.locator('.teams-hero-actions').getByRole('button', { name: /^Blueprints$/i }).click();
      await expect(blueprintEditor).toBeVisible();
      await blueprintList.getByRole('button', { name: /^Neu$/i }).click();
      await blueprintEditor.getByLabel('Name').fill(blueprintName);

      await blueprintEditor.getByRole('button', { name: /^Rolle hinzufuegen$/i }).click();
      await blueprintEditor.getByRole('button', { name: /^Rolle hinzufuegen$/i }).click();
      await blueprintEditor.getByLabel('Rollenname').nth(0).fill('Engineer');
      await blueprintEditor.getByLabel('Rollenname').nth(1).fill('Reviewer');
      await blueprintEditor.getByLabel('Sortierung').nth(0).fill('10');
      await blueprintEditor.getByLabel('Sortierung').nth(1).fill('10');
      await blueprintEditor.getByRole('button', { name: /^Erstellen$/i }).click();

      await expect(page.locator('.notification.error .notification-message')).toContainText(/Blueprint/i);

      await blueprintEditor.getByLabel('Sortierung').nth(1).fill('20');
      await blueprintEditor.getByRole('button', { name: /^Erstellen$/i }).click();
      await expect(page.locator('.notification.success .notification-message')).toHaveText(/Blueprint erstellt/i);

      const blueprintsRes = await requestWithRetry(request, 'get', `${hubUrl}/teams/blueprints`, { headers });
      expect(blueprintsRes.ok()).toBeTruthy();
      const blueprintsBody = await blueprintsRes.json();
      const blueprints = Array.isArray(blueprintsBody) ? blueprintsBody : (blueprintsBody?.data || []);
      const createdBlueprint = blueprints.find((blueprint: any) => blueprint.name === blueprintName);
      expect(createdBlueprint).toBeTruthy();
      createdBlueprintId = createdBlueprint.id;

      await page.locator('.teams-hero-actions').getByRole('button', { name: /^Aktualisieren$/i }).click();
      await expect(blueprintList.locator('.teams-blueprint-card').filter({ hasText: blueprintName }).first()).toBeVisible();
      await blueprintList.locator('.teams-blueprint-card').filter({ hasText: blueprintName }).first().click();
      await blueprintEditor.getByRole('button', { name: /^Fuer Team-Erstellung uebernehmen$/i }).click();
      await expect(instantiatePanel.getByRole('heading', { name: /^Team aus Blueprint erstellen$/i })).toBeVisible();
      await instantiatePanel.getByLabel('Teamname').fill(teamName);
      await instantiatePanel.getByRole('button', { name: /^Team erstellen$/i }).click();
      await expect(page.getByText('Team aus Blueprint erstellt', { exact: true })).toBeVisible();

      const teamsRes = await requestWithRetry(request, 'get', `${hubUrl}/teams`, { headers });
      expect(teamsRes.ok()).toBeTruthy();
      const teamsBody = await teamsRes.json();
      const teams = Array.isArray(teamsBody) ? teamsBody : (teamsBody?.data || []);
      const createdTeam = teams.find((team: any) => team.name === teamName);
      expect(createdTeam).toBeTruthy();
      expect(createdTeam.blueprint_id).toBe(createdBlueprintId);
      createdTeamId = createdTeam.id;
    } finally {
      if (createdTeamId) {
        await requestWithRetry(request, 'delete', `${hubUrl}/teams/${createdTeamId}`, { headers, attempts: 2 });
      }
      if (createdBlueprintId) {
        await requestWithRetry(request, 'delete', `${hubUrl}/teams/blueprints/${createdBlueprintId}`, { headers, attempts: 2 });
      }
    }
  });
});
