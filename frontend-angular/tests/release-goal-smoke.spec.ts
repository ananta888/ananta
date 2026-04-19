import { test, expect, type Page, type Route } from '@playwright/test';
import { assertNoUnhandledBrowserErrors, loginFast } from './utils';

type MockGoal = {
  id: string;
  summary: string;
  goal: string;
  status: string;
};

function json(data: unknown): string {
  return JSON.stringify({ status: 'success', data });
}

async function fulfillJson(route: Route, data: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: json(data) });
}

async function installGoalSmokeMocks(page: Page) {
  const goals: MockGoal[] = [{
    id: 'goal-existing',
    summary: 'Vorhandenes Release Goal',
    goal: 'Vorhandenes Release Goal',
    status: 'completed',
  }];
  let createdSequence = 0;

  await page.route('**/tasks/auto-planner/status*', route => fulfillJson(route, {
    enabled: true,
    stats: { goals_processed: goals.length, tasks_created: 2, followups_created: 0 },
  }));
  await page.route('**/goals/modes*', route => fulfillJson(route, [
    {
      id: 'diagnosis',
      title: 'Diagnose',
      description: 'Fehler und Sicherheitsgrenzen strukturiert pruefen.',
      fields: [
        { name: 'goal', label: 'Ziel', type: 'textarea' },
        { name: 'scope', label: 'Scope', type: 'text', default: 'frontend' },
      ],
    },
  ]));
  await page.route('**/goals/*/governance-summary*', route => {
    const goalId = route.request().url().match(/\/goals\/([^/]+)\/governance-summary/)?.[1] || 'goal';
    return fulfillJson(route, {
      goal_id: goalId,
      verification: { total: 2, passed: 2, failed: 0, escalated: 0 },
      policy: { approved: 1, blocked: 0 },
      cost_summary: { total_cost_units: 2.25, tasks_with_cost: 2, total_tokens: 900, total_latency_ms: 640 },
      summary: { task_count: 2 },
    });
  });
  await page.route('**/goals/*/detail*', route => {
    const goalId = route.request().url().match(/\/goals\/([^/]+)\/detail/)?.[1] || 'goal';
    return fulfillJson(route, {
      goal: { id: goalId, summary: 'Release Smoke Goal', goal: 'Release Smoke Goal', status: 'completed' },
      artifacts: {
        artifacts: [{ task_id: 'task-release-1', title: 'Release Report', preview: 'Smoke Ergebnis sichtbar' }],
        headline_artifact: { title: 'Wichtigstes Ergebnis', preview: 'Release Smoke bestanden' },
        result_summary: { completed_tasks: 2, failed_tasks: 0 },
      },
      governance: {
        verification: { total: 2, passed: 2, failed: 0, escalated: 0 },
        policy: { approved: 1, blocked: 0 },
      },
      cost_summary: { total_cost_units: 2.25, total_tokens: 900 },
      tasks: [
        { id: 'task-release-1', title: 'Plan pruefen', status: 'completed', verification_status: { status: 'passed' } },
        { id: 'task-release-2', title: 'Ergebnis sichern', status: 'completed', verification_status: { status: 'passed' } },
      ],
    });
  });
  await page.route('**/goals', async route => {
    if (route.request().method() === 'GET') {
      await fulfillJson(route, goals);
      return;
    }
    if (route.request().method() === 'POST') {
      createdSequence += 1;
      const payload = route.request().postDataJSON() as any;
      const id = `goal-guided-${createdSequence}`;
      goals.unshift({
        id,
        summary: payload?.mode_data?.goal || 'Gefuehrtes Smoke Goal',
        goal: payload?.mode_data?.goal || 'Gefuehrtes Smoke Goal',
        status: 'planned',
      });
      await fulfillJson(route, {
        goal: { id, summary: payload?.mode_data?.goal || 'Gefuehrtes Smoke Goal', status: 'planned' },
        created_task_ids: [`task-${id}-1`, `task-${id}-2`],
      });
      return;
    }
    await route.continue();
  });
  await page.route('**/tasks/auto-planner/plan', async route => {
    const payload = route.request().postDataJSON() as any;
    createdSequence += 1;
    const id = `goal-quick-${createdSequence}`;
    goals.unshift({
      id,
      summary: payload?.goal || 'Quick Smoke Goal',
      goal: payload?.goal || 'Quick Smoke Goal',
      status: 'planned',
    });
    await fulfillJson(route, {
      goal_id: id,
      created_task_ids: [`task-${id}-1`, `task-${id}-2`],
      subtasks: [{ title: 'Analyse' }, { title: 'Verifikation' }],
    }, 201);
  });

  return { goals };
}

test.describe('Release goal smoke E2E', () => {
  test('logs in, creates the first quick goal and opens the result summary', async ({ page, request }) => {
    await loginFast(page, request);
    await installGoalSmokeMocks(page);

    await page.goto('/dashboard');
    const quickGoal = page.locator('#quick-goal');
    await expect(quickGoal.getByLabel('Quick Goal Beschreibung eingeben')).toBeVisible();
    await quickGoal.getByLabel('Quick Goal Beschreibung eingeben').fill('Release Smoke Goal planen');
    await quickGoal.getByRole('button', { name: /Goal planen/i }).click();
    await expect(quickGoal).toContainText('Goal wurde geplant');
    await quickGoal.getByRole('button', { name: /Zum Goal Detail/i }).click();

    await expect(page.getByRole('heading', { name: /Release Smoke Goal/i })).toBeVisible();
    await expect(page.locator('.result-summary')).toContainText('Goal abgeschlossen');
    await expect(page.locator('.result-summary')).toContainText('Wichtigstes Ergebnis');
    await expect(page.getByText('Governance & Kosten')).toBeVisible();
    await assertNoUnhandledBrowserErrors(page);
  });

  test('runs guided goal creation and exposes safety plus review notices', async ({ page, request }) => {
    await loginFast(page, request);
    await installGoalSmokeMocks(page);

    await page.goto('/dashboard');
    await expect(page.getByRole('heading', { name: /Gefuehrter Ziel-Assistent/i })).toBeVisible();
    const wizard = page.locator('app-dashboard-guided-goal-wizard');
    await wizard.getByRole('button', { name: /Diagnose/i }).click();
    await wizard.locator('textarea').first().fill('Blockierten Release-Pfad mit Review-Hinweis pruefen');
    await wizard.getByRole('button', { name: /^Weiter$/i }).click();
    await wizard.locator('textarea').first().fill('E2E-Kontext: Policy-Grenze und Review-Pflicht sollen sichtbar bleiben.');
    await wizard.getByRole('button', { name: /^Weiter$/i }).click();
    await wizard.getByRole('button', { name: /Gruendlich/i }).click();
    await wizard.getByRole('button', { name: /^Weiter$/i }).click();
    await wizard.getByRole('button', { name: /Vorsichtig/i }).click();
    await wizard.getByRole('button', { name: /^Weiter$/i }).click();
    await expect(page.getByText(/Bereit zum Planen/i)).toBeVisible();
    await expect(page.getByText(/Sicherheit/i)).toBeVisible();
    await wizard.getByRole('button', { name: /Goal planen/i }).click();

    await expect(page.locator('#quick-goal')).toContainText('Goal wurde geplant');
    await expect(page.locator('#quick-goal')).toContainText('2 Tasks erstellt');
    await assertNoUnhandledBrowserErrors(page);
  });
});
