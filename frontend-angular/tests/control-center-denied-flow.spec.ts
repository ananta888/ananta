import { expect, test } from '@playwright/test';
import { loginFast } from './utils';

test.describe('Control Center denied flow', () => {
  test('shows denied tool call in timeline and policy log without manual polling', async ({ page, request }) => {
    await loginFast(page, request);

    await page.route('**/api/sessions', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { items: [{ id: 'sess-deny', task_id: 'task-deny', owner_user_id: 'worker-x', transport: 'hub_relay', status: 'running' }], count: 1 } }),
      });
    });

    await page.route('**/api/tasks/task-deny', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { task: { id: 'task-deny' }, verification: { status: 'failed', test_count: 3, passed_count: 2, failed_count: 1 } } }),
      });
    });

    await page.route('**/api/sessions/sess-deny/policy-decisions', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { items: [{ id: 'dec-1', decision: 'deny', decision_type: 'tool_policy', reason: 'tool blocked', matched_rule_ids: ['RULE-DENY-1'], action_id: 'act-1', tool_call_id: 'tc-1', created_at: Date.now() / 1000 }], count: 1 } }),
      });
    });

    await page.goto('/control-center/policies', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Policies & Approvals')).toBeVisible();
    await expect(page.getByText('tool blocked')).toBeVisible();

    await page.goto('/control-center/sessions', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText('Sessions')).toBeVisible();
    await expect(page.getByText('Event Stream:')).toBeVisible();
    await expect(page.getByText('failed')).toBeVisible();
  });
});
