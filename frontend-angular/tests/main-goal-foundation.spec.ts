import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { HUB_URL, assertNoUnhandledBrowserErrors, assertErrorOverlaysInViewport, loginFast } from './utils';

function unwrapList(body: any): any[] {
  if (Array.isArray(body)) return body;
  if (Array.isArray(body?.data)) return body.data;
  if (Array.isArray(body?.items)) return body.items;
  return [];
}

async function apiDelete(request: APIRequestContext, url: string, token: string | null) {
  try {
    await request.delete(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
  } catch {}
}

async function getHubInfo(page: Page): Promise<{
  hubUrl: string;
  token: string | null;
  workerAgentUrls: string[];
}> {
  return page.evaluate((defaultHubUrl: string) => {
    const token = localStorage.getItem('ananta.user.token');
    const raw = localStorage.getItem('ananta.agents.v1');
    let hubUrl = defaultHubUrl;
    let workerAgentUrls: string[] = [];
    if (raw) {
      try {
        const agents = JSON.parse(raw);
        const hub = agents.find((a: any) => a.role === 'hub');
        if (hub?.url) hubUrl = hub.url;
        workerAgentUrls = agents
          .filter((a: any) => a.role !== 'hub' && a.url)
          .map((a: any) => String(a.url));
      } catch {}
    }
    if (!hubUrl || hubUrl === 'undefined') hubUrl = defaultHubUrl;
    return { hubUrl, token, workerAgentUrls };
  }, HUB_URL);
}

test.describe('Main Goal UI Foundation', () => {
  test('canonical UI control points are present', async ({ page, request }) => {
    await loginFast(page, request);

    await page.goto('/templates');
    await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Anlegen \/ Speichern/i })).toBeVisible();

    await page.goto('/teams');
    await expect(page.getByRole('heading', { name: /Teams werden ueber Blueprints erstellt/i })).toBeVisible();
    await expect(page.locator('.teams-hero-actions').getByRole('button', { name: /^Blueprints$/i })).toBeVisible();
    await expect(page.locator('.teams-tablist').getByRole('button', { name: /^Teams aus Blueprint$/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /^Aktualisieren$/i })).toBeEnabled();

    await page.goto('/auto-planner');
    await expect(page.getByTestId('auto-planner-goal-input')).toBeVisible();
    await expect(page.getByTestId('auto-planner-goal-plan')).toBeVisible();

    await assertErrorOverlaysInViewport(page);
    await assertNoUnhandledBrowserErrors(page);
  });

  test('foundation journey creates template + blueprint + team with two workers via UI', async ({ page, request }) => {
    test.setTimeout(150_000);
    await loginFast(page, request);
    const { hubUrl, token, workerAgentUrls } = await getHubInfo(page);

    const templateName = `E2E Main Template ${Date.now()}`;
    const blueprintName = `E2E Main Blueprint ${Date.now()}`;
    const teamName = `E2E Main Team ${Date.now()}`;
    let createdTemplateId: string | null = null;
    let createdBlueprintId: string | null = null;
    let createdTeamId: string | null = null;
    let capturedInstantiateMemberCount = 0;

    try {
      await page.goto('/templates');
      await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();
      await page.getByPlaceholder('Name').fill(templateName);
      await page.getByPlaceholder('Beschreibung').fill('Template fuer Main-Goal Foundation Journey');
      await page.locator('textarea[placeholder*="Platzhalter"]').fill('Du bist {{agent_name}} und erledigst {{task_title}}.');
      await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();

      await expect.poll(async () => {
        const res = await request.get(`${hubUrl}/templates`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        });
        if (!res.ok()) return '';
        const templates = unwrapList(await res.json());
        return templates.find((tpl: any) => tpl.name === templateName)?.id || '';
      }, { timeout: 20_000 }).not.toBe('');

      const templateRes = await request.get(`${hubUrl}/templates`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      const templates = unwrapList(await templateRes.json());
      createdTemplateId = templates.find((tpl: any) => tpl.name === templateName)?.id || null;
      expect(createdTemplateId).toBeTruthy();

      await page.goto('/teams');
      await expect(page.getByText(/Blueprint-first Teams/i)).toBeVisible();
      const editor = page.locator('.teams-editor-panel');
      await expect(editor).toBeVisible();
      await editor.getByLabel('Name').fill(blueprintName);
      await editor.getByLabel('Beschreibung').fill('Blueprint fuer Main-Goal Foundation Journey');
      await editor.getByRole('button', { name: /Rolle hinzufuegen/i }).click();
      await editor.getByLabel('Rollenname').first().fill('Implementer');
      await editor.getByLabel('Template').first().selectOption(String(createdTemplateId));
      await editor.getByRole('button', { name: /^Erstellen$/i }).click();

      await expect.poll(async () => {
        const res = await request.get(`${hubUrl}/teams/blueprints`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        });
        if (!res.ok()) return '';
        const blueprints = unwrapList(await res.json());
        return blueprints.find((bp: any) => bp.name === blueprintName)?.id || '';
      }, { timeout: 20_000 }).not.toBe('');

      const blueprintsRes = await request.get(`${hubUrl}/teams/blueprints`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      const blueprints = unwrapList(await blueprintsRes.json());
      createdBlueprintId = blueprints.find((bp: any) => bp.name === blueprintName)?.id || null;
      expect(createdBlueprintId).toBeTruthy();

      await page.getByRole('button', { name: /^Teams aus Blueprint$/i }).click();
      const instantiateCard = page.locator('.card.card-success').first();
      await expect(instantiateCard.getByLabel('Blueprint')).toBeVisible();
      await instantiateCard.getByLabel('Blueprint').selectOption(String(createdBlueprintId));
      await instantiateCard.getByLabel('Teamname').fill(teamName);

      await page.route('**/teams/blueprints/*/instantiate', async route => {
        const payload = route.request().postDataJSON() as any;
        capturedInstantiateMemberCount = Array.isArray(payload?.members) ? payload.members.length : 0;
        await route.continue();
      });

      if (workerAgentUrls.length >= 2) {
        await instantiateCard.getByRole('button', { name: /Mitglied hinzufuegen/i }).click();
        await instantiateCard.getByRole('button', { name: /Mitglied hinzufuegen/i }).click();
        const agentSelects = instantiateCard.getByLabel('Agent');
        await expect(agentSelects).toHaveCount(2);
        await agentSelects.nth(0).selectOption(workerAgentUrls[0]);
        await agentSelects.nth(1).selectOption(workerAgentUrls[1]);
      }

      await instantiateCard.getByRole('button', { name: /^Team erstellen$/i }).click();

      await expect.poll(async () => {
        const res = await request.get(`${hubUrl}/teams`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        });
        if (!res.ok()) return '';
        const teams = unwrapList(await res.json());
        return teams.find((t: any) => t.name === teamName)?.id || '';
      }, { timeout: 25_000 }).not.toBe('');

      if (workerAgentUrls.length >= 2) {
        expect(capturedInstantiateMemberCount).toBeGreaterThanOrEqual(2);
      }

      const teamsRes = await request.get(`${hubUrl}/teams`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      const teams = unwrapList(await teamsRes.json());
      createdTeamId = teams.find((t: any) => t.name === teamName)?.id || null;
      expect(createdTeamId).toBeTruthy();

      await assertErrorOverlaysInViewport(page);
      await assertNoUnhandledBrowserErrors(page);
    } finally {
      if (createdTeamId) await apiDelete(request, `${hubUrl}/teams/${createdTeamId}`, token);
      if (createdBlueprintId) await apiDelete(request, `${hubUrl}/teams/blueprints/${createdBlueprintId}`, token);
      if (createdTemplateId) await apiDelete(request, `${hubUrl}/templates/${createdTemplateId}`, token);
    }
  });
});
