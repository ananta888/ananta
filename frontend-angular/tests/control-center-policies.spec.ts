import { test, expect } from '@playwright/test';
import { loginFast } from './utils';

test.describe('Control Center Policy View', () => {
  test('shows denied actions and narrow approval payload', async ({ page, request }) => {
    await loginFast(page, request);

    await page.route('**/api/sessions', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            items: [{ id: 'sess-policy', task_id: 'task-1', owner_user_id: 'worker-x', transport: 'hub_relay', status: 'running' }],
            count: 1,
          },
        }),
      });
    });
    await page.route('**/api/sessions/sess-policy/policy-decisions', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            items: [
              { id: 'dec-allow', decision: 'allow', decision_type: 'tool_policy', reason: 'safe action', matched_rule_ids: [], action_id: 'act-ok', tool_call_id: 'tc-ok', created_at: Date.now() / 1000 },
              { id: 'dec-pending', decision: 'require_approval', decision_type: 'tool_call_gate', reason: 'tool blocked', matched_rule_ids: ['RULE-DENY-1'], action_id: 'approve:s1', tool_call_id: 'tc-103', created_at: Date.now() / 1000 },
            ],
            count: 2,
          },
        }),
      });
    });
    await page.route('**/api/policy/approve', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { ok: true } }),
      });
    });

    await page.goto('/control-center/policies', { waitUntil: 'domcontentloaded' });

    await expect(page.getByText('Policies & Approvals')).toBeVisible();
    await expect(page.getByText('tool blocked · Rules', { exact: false }).first()).toBeVisible();
    await page.getByRole('button', { name: 'Narrow Approval senden' }).click();

    const payload = page.locator('pre.raw');
    await expect(payload).toContainText('"scope": "single_action"');
    await expect(payload).toContainText('"action_id": "approve:s1"');
    await expect(payload).toContainText('"tool_call_id": "tc-103"');
  });
});
