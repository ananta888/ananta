import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { HUB_URL, assertNoUnhandledBrowserErrors, loginFast } from './utils';

async function getHubInfo(page: Page): Promise<{ hubUrl: string; token: string | null }> {
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

function unwrapList(body: any): any[] {
  if (Array.isArray(body)) return body;
  if (Array.isArray(body?.data)) return body.data;
  if (Array.isArray(body?.items)) return body.items;
  return [];
}

async function apiDelete(request: APIRequestContext, url: string, token: string | null) {
  await request.delete(url, {
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });
}

test.describe('UI UX Workflows', () => {
  test('goal flow remains interactive and renders details', async ({ page, request }) => {
    await loginFast(page, request);

    const goals: any[] = [];
    await page.route('**/tasks/auto-planner/status*', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            enabled: true,
            auto_followup_enabled: false,
            auto_start_autopilot: false,
            max_subtasks_per_goal: 5,
            default_priority: 'Medium',
            llm_timeout: 30,
            stats: { goals_processed: goals.length, tasks_created: goals.length * 2, followups_created: 0 },
          },
        }),
      });
    });
    await page.route('**/teams*', async route => {
      if (!route.request().url().includes('/teams?') && !route.request().url().endsWith('/teams')) {
        await route.continue();
        return;
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: [] }) });
    });
    await page.route('**/goals', async route => {
      if (route.request().method() === 'GET') {
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: goals }) });
        return;
      }
      if (route.request().method() === 'POST') {
        const payload = route.request().postDataJSON() as any;
        const id = `goal-ui-${Date.now()}`;
        const created = {
          goal: { id, summary: payload?.goal || 'Goal', status: 'planned' },
          subtask_count: 2,
          created_task_ids: [`task-${Date.now()}-1`, `task-${Date.now()}-2`],
          subtasks: [
            { title: 'Analyse', priority: 'Medium' },
            { title: 'Implementierung', priority: 'High' },
          ],
          plan_id: `plan-${Date.now()}`,
        };
        goals.unshift({ id, summary: payload?.goal || 'Goal', goal: payload?.goal || 'Goal', status: 'planned' });
        await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data: created }) });
        return;
      }
      await route.continue();
    });
    await page.route('**/goals/*/detail*', async route => {
      const goalIdMatch = route.request().url().match(/\/goals\/([^/]+)\/detail/);
      const goalId = goalIdMatch?.[1] || 'goal';
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            goal: { id: goalId, summary: 'UI Goal', status: 'planned' },
            trace: { trace_id: `trace-${goalId}` },
            artifacts: {
              result_summary: { completed_tasks: 1, failed_tasks: 0 },
              headline_artifact: { preview: 'Artifact preview from UI workflow test' },
            },
            plan: {
              nodes: [{ id: 'node-1', title: 'Analyse', status: 'draft', priority: 'Medium', node_key: 'node-1' }],
            },
            governance: {
              policy: { total: 1, approved: 1, blocked: 0 },
              verification: { total: 1, passed: 1, escalated: 0 },
              summary: { governance_visible: true, detail_level: 'full' },
            },
            tasks: [{ id: 'task-1', title: 'Analyse', status: 'todo', trace_id: `trace-${goalId}` }],
          },
        }),
      });
    });

    await page.goto('/auto-planner');
    await expect(page.getByTestId('auto-planner-goal-input')).toBeVisible();
    await page.getByTestId('auto-planner-goal-input').fill(`UI Workflow Goal ${Date.now()}`);
    await page.getByTestId('auto-planner-goal-plan').click();
    await expect(page.getByTestId('goal-submit-result')).toBeVisible({ timeout: 15000 });
    await expect(page.getByTestId('goal-list')).toContainText(/UI Workflow Goal/i);
    await page.getByTestId('goal-list').locator('.ap-recent-item').first().click();
    await expect(page.getByTestId('goal-detail-panel')).toBeVisible();
    await expect(page.getByTestId('goal-plan-panel')).toContainText('Analyse');
    await assertNoUnhandledBrowserErrors(page);
  });

  test('templates + blueprint + team creation works via UI', async ({ page, request }) => {
    await loginFast(page, request);
    const { hubUrl, token } = await getHubInfo(page);

    const templateName = `E2E UI Template ${Date.now()}`;
    const blueprintName = `E2E UI Blueprint ${Date.now()}`;
    const teamName = `E2E UI Team ${Date.now()}`;

    let createdTemplateId: string | null = null;
    let createdBlueprintId: string | null = null;
    let createdTeamId: string | null = null;

    try {
      await page.goto('/templates');
      await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();
      await page.getByPlaceholder('Name').fill(templateName);
      await page.getByPlaceholder('Beschreibung').fill('Template aus UI-Workflow-Test');
      await page.locator('textarea[placeholder*="Platzhalter"]').fill('Du bist {{agent_name}} und bearbeitest {{task_title}}.');
      await page.getByRole('button', { name: /Anlegen \/ Speichern/i }).click();

      await expect.poll(async () => {
        const templateListRes = await request.get(`${hubUrl}/templates`, {
          headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        });
        if (!templateListRes.ok()) return '';
        const templates = unwrapList(await templateListRes.json());
        return templates.find((t: any) => t.name === templateName)?.id || '';
      }, { timeout: 15000 }).not.toBe('');

      const templateListRes = await request.get(`${hubUrl}/templates`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      expect(templateListRes.ok()).toBeTruthy();
      const templates = unwrapList(await templateListRes.json());
      createdTemplateId = templates.find((t: any) => t.name === templateName)?.id || null;
      expect(createdTemplateId).toBeTruthy();
      await page.getByRole('button', { name: /^Aktualisieren$/i }).click();
      await expect(page.getByText(templateName).first()).toBeVisible({ timeout: 15000 });

      await page.goto('/teams');
      await expect(page.getByText(/Blueprint-first Teams/i)).toBeVisible();
      await expect(page.getByRole('button', { name: /^Aktualisieren$/i })).toBeEnabled({ timeout: 20000 });

      await page.getByRole('button', { name: /^Neu$/i }).click();
      const editor = page.locator('.teams-editor-panel');
      await editor.getByLabel('Name').fill(blueprintName);
      await editor.getByLabel('Beschreibung').fill('Blueprint aus UI-Workflow-Test');
      await editor.getByRole('button', { name: /Rolle hinzufuegen/i }).click();
      await editor.getByLabel('Rollenname').first().fill('Implementer');
      await editor.getByLabel('Template').first().selectOption({ label: templateName });
      await editor.getByRole('button', { name: /^Erstellen$/i }).click();

      await expect(page.locator('.teams-blueprint-card', { hasText: blueprintName }).first()).toBeVisible({ timeout: 15000 });

      const blueprintsRes = await request.get(`${hubUrl}/teams/blueprints`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      expect(blueprintsRes.ok()).toBeTruthy();
      const blueprints = unwrapList(await blueprintsRes.json());
      createdBlueprintId = blueprints.find((b: any) => b.name === blueprintName)?.id || null;
      expect(createdBlueprintId).toBeTruthy();

      await page.getByRole('button', { name: /^Teams aus Blueprint$/i }).click();
      const instantiateCard = page.locator('.card.card-success').first();
      await instantiateCard.getByLabel('Blueprint').selectOption({ label: blueprintName });
      await instantiateCard.getByLabel('Teamname').fill(teamName);
      await instantiateCard.getByRole('button', { name: /^Team erstellen$/i }).click();

      await expect(page.locator('.teams-team-card', { hasText: teamName }).first()).toBeVisible({ timeout: 20000 });

      const teamsRes = await request.get(`${hubUrl}/teams`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
      });
      expect(teamsRes.ok()).toBeTruthy();
      const teams = unwrapList(await teamsRes.json());
      createdTeamId = teams.find((t: any) => t.name === teamName)?.id || null;
      expect(createdTeamId).toBeTruthy();
      await assertNoUnhandledBrowserErrors(page);
    } finally {
      if (createdTeamId) {
        await apiDelete(request, `${hubUrl}/teams/${createdTeamId}`, token);
      }
      if (createdBlueprintId) {
        await apiDelete(request, `${hubUrl}/teams/blueprints/${createdBlueprintId}`, token);
      }
      if (createdTemplateId) {
        await apiDelete(request, `${hubUrl}/templates/${createdTemplateId}`, token);
      }
    }
  });

  test('teams page unlocks even when one backend call hangs', async ({ page, request }) => {
    test.setTimeout(70_000);
    await loginFast(page, request);

    await page.route('**/teams/roles*', async route => {
      await new Promise((resolve) => setTimeout(resolve, 30_000));
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify([]) });
    });

    await page.goto('/teams');
    await expect(page.getByText(/Blueprint-first Teams/i)).toBeVisible();
    await expect(page.getByRole('button', { name: /^Aktualisieren$/i })).toBeEnabled({ timeout: 25_000 });
    await expect(page.getByRole('heading', { name: /Blueprint bearbeiten|Neuen Blueprint anlegen/i })).toBeVisible({ timeout: 25_000 });
    await assertNoUnhandledBrowserErrors(page);
  });
});
