import { test, expect, type Page, type Route } from '@playwright/test';
import { assertErrorOverlaysInViewport, assertNoUnhandledBrowserErrors, clearBrowserErrorGuards, loginFast } from './utils';

function body(data: unknown): string {
  return JSON.stringify({ status: 'success', data });
}

async function ok(route: Route, data: unknown): Promise<void> {
  await route.fulfill({ status: 200, contentType: 'application/json', body: body(data) });
}

async function installLiveClickMocks(page: Page): Promise<void> {
  const goals = [{
    id: 'goal-live-1',
    summary: 'Live Click Goal',
    goal: 'Live Click Goal',
    status: 'in_progress',
  }];

  await page.route('**/dashboard/read-model*', route => ok(route, {
    context_timestamp: Math.floor(Date.now() / 1000),
    system_health: { agent: 'hub', status: 'ok', checks: {} },
    contracts: { task_statuses: { canonical_values: ['todo', 'in_progress', 'completed'] } },
    agents: { count: 1, items: [{ name: 'hub', status: 'online' }] },
    teams: { items: [] },
    roles: { items: [] },
    benchmarks: { items: [], task_kind: 'analysis' },
    llm_configuration: {},
  }));
  await page.route('**/api/system/stats/history*', route => ok(route, []));
  await page.route('**/teams/roles*', route => ok(route, []));
  await page.route('**/teams', route => ok(route, []));
  await page.route('**/teams?*', route => ok(route, []));
  await page.route('**/api/system/agents*', route => ok(route, [{ name: 'hub', status: 'online' }]));
  await page.route('**/tasks', route => route.request().method() === 'GET'
    ? ok(route, [{ id: 'task-live-1', title: 'Analyse', status: 'completed' }])
    : route.fallback()
  );
  await page.route('**/tasks?*', route => route.request().method() === 'GET'
    ? ok(route, [{ id: 'task-live-1', title: 'Analyse', status: 'completed' }])
    : route.fallback()
  );
  await page.route('**/tasks/auto-planner/status*', route => ok(route, {
    enabled: true,
    stats: { goals_processed: 1, tasks_created: 2, followups_created: 0 },
  }));
  await page.route('**/goals/modes*', route => ok(route, []));
  await page.route('**/goals/*/governance-summary*', route => ok(route, {
    goal_id: 'goal-live-1',
    verification: { total: 2, passed: 1, failed: 0, escalated: 1 },
    policy: { approved: 1, blocked: 1 },
    cost_summary: { total_cost_units: 1.5, tasks_with_cost: 1, total_tokens: 512, total_latency_ms: 420 },
    summary: { task_count: 2 },
  }));
  await page.route('**/goals/*/detail*', route => ok(route, {
    goal: { id: 'goal-live-1', summary: 'Live Click Goal', goal: 'Live Click Goal', status: 'in_progress' },
    artifacts: {
      artifacts: [{ task_id: 'task-live-1', title: 'Zwischenstand', preview: 'Teilweise geprueft' }],
      result_summary: { completed_tasks: 1, failed_tasks: 0 },
    },
    governance: {
      verification: { total: 2, passed: 1, failed: 0, escalated: 1 },
      policy: { approved: 1, blocked: 1 },
    },
    cost_summary: { total_cost_units: 1.5, total_tokens: 512 },
    tasks: [
      { id: 'task-live-1', title: 'Analyse', status: 'completed', verification_status: { status: 'passed' } },
      { id: 'task-live-2', title: 'Review erforderlich', status: 'todo', verification_status: { status: 'review_required' } },
    ],
  }));
  await page.route('**/goals', route => route.request().method() === 'GET' ? ok(route, goals) : route.fallback());
  await page.route(/\/goals\/goal-live-1\/governance-summary(?:\?.*)?$/, route => ok(route, {
    goal_id: 'goal-live-1',
    verification: { total: 2, passed: 1, failed: 0, escalated: 1 },
    policy: { approved: 1, blocked: 1 },
    cost_summary: { total_cost_units: 1.5, tasks_with_cost: 1, total_tokens: 512, total_latency_ms: 420 },
    summary: { task_count: 2 },
  }));
  await page.route(/\/goals\/goal-live-1\/detail(?:\?.*)?$/, route => ok(route, {
    goal: { id: 'goal-live-1', summary: 'Live Click Goal', goal: 'Live Click Goal', status: 'in_progress' },
    artifacts: {
      artifacts: [{ task_id: 'task-live-1', title: 'Zwischenstand', preview: 'Teilweise geprueft' }],
      result_summary: { completed_tasks: 1, failed_tasks: 0 },
    },
    governance: {
      verification: { total: 2, passed: 1, failed: 0, escalated: 1 },
      policy: { approved: 1, blocked: 1 },
    },
    cost_summary: { total_cost_units: 1.5, total_tokens: 512 },
    tasks: [
      { id: 'task-live-1', title: 'Analyse', status: 'completed', verification_status: { status: 'passed' } },
      { id: 'task-live-2', title: 'Review erforderlich', status: 'todo', verification_status: { status: 'review_required' } },
    ],
  }));
}

test.describe('Live click critical paths', () => {
  test('clicks dashboard, goal start and result reading path with stable selectors', async ({ page, request }) => {
    await page.addInitScript(() => {
      localStorage.setItem('ananta.dashboard.advanced', 'true');
      localStorage.setItem('ananta.first-start.completed', 'true');
      localStorage.setItem('ananta.ai-assistant.minimized.v1', 'true');
    });
    await installLiveClickMocks(page);
    await loginFast(page, request);

    await page.goto('/dashboard#quick-goal');
    await expect(page.getByRole('heading', { name: /System Dashboard|Ananta starten/i })).toBeVisible();
    await page.getByRole('button', { name: /Diagnostizieren/i }).click();
    await expect(page.locator('#quick-goal').getByLabel('Zielbeschreibung eingeben')).toBeVisible();
    await expect(page.getByText(/Goal Governance & Cost Summary/i)).toBeVisible();
    await expect(page.getByRole('combobox', { name: /Goal fuer Governance Summary/i })).toContainText('Live Click Goal');
    await expect(page.getByRole('button', { name: /Live Click Goal in_progress/i })).toBeVisible();

    await page.goto('/goal/goal-live-1');
    await expect(page.getByRole('heading', { name: /Live Click Goal/i })).toBeVisible();
    await expect(page.locator('.result-summary')).toContainText('Goal ist in Arbeit');
    await expect(page.locator('.result-summary')).toContainText('Noch sind nicht alle Aufgaben fertig');
    await assertErrorOverlaysInViewport(page);
    await assertNoUnhandledBrowserErrors(page);
  });

  test('surfaces blocked quick-goal submission and recovers on retry', async ({ page, request }) => {
    await page.addInitScript(() => {
      localStorage.setItem('ananta.dashboard.advanced', 'true');
      localStorage.setItem('ananta.first-start.completed', 'true');
      localStorage.setItem('ananta.ai-assistant.minimized.v1', 'true');
    });
    await installLiveClickMocks(page);
    await loginFast(page, request);
    let attempts = 0;

    await page.route('**/tasks/auto-planner/plan', async route => {
      attempts += 1;
      if (attempts === 1) {
        await route.fulfill({
          status: 400,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'error',
            message: 'policy_blocked_review_required',
            data: { reason: 'review_required', blocked_reasons: ['scope_requires_review'] },
          }),
        });
        return;
      }
      await route.fulfill({
        status: 201,
        contentType: 'application/json',
        body: body({
          goal_id: 'goal-live-recovered',
          created_task_ids: ['task-recovered-1'],
          subtasks: [{ title: 'Review nachholen' }],
        }),
      });
    });

    await page.goto('/dashboard#quick-goal');
    const quickGoal = page.locator('#quick-goal');
    await quickGoal.getByLabel('Zielbeschreibung eingeben').fill('Riskanten Pfad mit Review pruefen');
    await quickGoal.getByRole('button', { name: /Goal planen/i }).click();
    await expect(page.getByText(/Planung fehlgeschlagen/i)).toBeVisible();
    await assertErrorOverlaysInViewport(page);
    clearBrowserErrorGuards(page);

    await quickGoal.getByRole('button', { name: /Goal planen/i }).click();
    await expect(quickGoal).toContainText('Plan steht bereit');
    await expect(quickGoal).toContainText('1 Aufgaben');
    expect(attempts).toBe(2);
    await assertNoUnhandledBrowserErrors(page);
  });
});
