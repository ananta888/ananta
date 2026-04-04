import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import { HUB_URL, assertNoUnhandledBrowserErrors, assertErrorOverlaysInViewport, loginFast } from './utils';

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

async function apiDelete(request: APIRequestContext, url: string, token: string | null) {
  try {
    await request.delete(url, {
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    });
  } catch {}
}

test.describe('Main Goal UI Planning', () => {
  test('plans goal via UI with explicit team mapping', async ({ page, request }) => {
    test.setTimeout(120_000);
    await loginFast(page, request);
    const { hubUrl, token } = await getHubInfo(page);

    const teamName = `E2E Planning Team ${Date.now()}`;
    let createdTeamId: string | null = null;
    let capturedGoalPayload: any = null;
    const mockedGoals: any[] = [];

    try {
      const setupTeamRes = await request.post(`${hubUrl}/teams/setup-scrum`, {
        headers: token ? { Authorization: `Bearer ${token}` } : undefined,
        data: { name: teamName },
      });
      expect(setupTeamRes.ok(), `setup-scrum failed: ${setupTeamRes.status()}`).toBeTruthy();
      const setupBody = await setupTeamRes.json();
      createdTeamId = setupBody?.data?.team?.id || setupBody?.team?.id || null;
      expect(createdTeamId).toBeTruthy();

      await page.route('**/goals', async route => {
        const method = route.request().method();
        if (method === 'GET') {
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({ status: 'success', data: mockedGoals }),
          });
          return;
        }
        if (method === 'POST') {
          capturedGoalPayload = route.request().postDataJSON();
          const id = `goal-planning-ui-${Date.now()}`;
          mockedGoals.unshift({
            id,
            summary: capturedGoalPayload?.goal || 'UI Goal',
            goal: capturedGoalPayload?.goal || 'UI Goal',
            status: 'planned',
          });
          await route.fulfill({
            status: 200,
            contentType: 'application/json',
            body: JSON.stringify({
              status: 'success',
              data: {
                goal: { id, summary: capturedGoalPayload?.goal || 'UI Goal', status: 'planned' },
                subtask_count: 1,
                created_task_ids: [`task-${Date.now()}-1`],
                subtasks: [{ title: 'Analyse', priority: 'Medium' }],
                plan_id: `plan-${Date.now()}`,
              },
            }),
          });
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
              goal: { id: goalId, summary: 'UI Planned Goal', status: 'planned' },
              trace: { trace_id: `trace-${goalId}` },
              artifacts: {
                result_summary: { completed_tasks: 0, failed_tasks: 0 },
                headline_artifact: { preview: 'Planning preview' },
              },
              plan: {
                nodes: [{ id: 'node-1', title: 'Analyse', status: 'draft', priority: 'Medium', node_key: 'node-1' }],
              },
              governance: {
                policy: { total: 1, approved: 1, blocked: 0 },
                verification: { total: 0, passed: 0, escalated: 0 },
                summary: { governance_visible: true, detail_level: 'full' },
              },
              tasks: [],
            },
          }),
        });
      });

      await page.goto('/auto-planner');
      await expect(page.getByTestId('auto-planner-goal-input')).toBeVisible();
      await page.locator('label:has-text("Team") select').selectOption(String(createdTeamId));
      await page.getByTestId('auto-planner-goal-input').fill(`UI Planning Goal ${Date.now()}`);
      await expect(page.getByTestId('auto-planner-goal-plan')).toBeEnabled();
      await page.getByTestId('auto-planner-goal-plan').click();

      await expect(page.getByTestId('goal-submit-result')).toBeVisible({ timeout: 20_000 });
      await expect(page.getByTestId('goal-list').locator('.ap-recent-item').first()).toBeVisible();
      await page.getByTestId('goal-list').locator('.ap-recent-item').first().click();
      await expect(page.getByTestId('goal-detail-panel')).toBeVisible();
      await expect(page.getByTestId('goal-plan-panel')).toContainText('Analyse');

      expect(capturedGoalPayload?.team_id).toBe(String(createdTeamId));
      await assertErrorOverlaysInViewport(page);
      await assertNoUnhandledBrowserErrors(page);
    } finally {
      if (createdTeamId) await apiDelete(request, `${hubUrl}/teams/${createdTeamId}`, token);
    }
  });
});
