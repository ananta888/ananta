import { test, expect } from '@playwright/test';
import { mockJson } from './helpers/mock-http';
import { ADMIN_PASSWORD, ADMIN_USERNAME } from './utils';

test.describe('Auto-Planner', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[name="username"]', ADMIN_USERNAME);
    await page.fill('input[name="password"]', ADMIN_PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
  });

  test('displays auto-planner page', async ({ page }) => {
    await page.goto('/auto-planner');
    await expect(page.locator('h3')).toContainText('Goal Workspace');
  });

  test('shows status cards', async ({ page }) => {
    await page.goto('/auto-planner');
    await expect(page.getByRole('heading', { name: 'Goal Workspace' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Konfiguration' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Goal erfassen' })).toBeVisible();
  });

  test('has configuration form', async ({ page }) => {
    await page.goto('/auto-planner');
    await expect(page.getByTestId('auto-planner-config-title')).toBeVisible();
    await expect(page.getByTestId('auto-planner-config-enabled')).toBeVisible();
    await expect(page.getByTestId('auto-planner-config-followups')).toBeVisible();
    await expect(page.getByTestId('auto-planner-config-autostart')).toBeVisible();
  });

  test('has goal input form', async ({ page }) => {
    await page.goto('/auto-planner');
    await expect(page.getByTestId('auto-planner-goal-title')).toBeVisible();
    await expect(page.getByTestId('auto-planner-goal-input')).toBeVisible();
  });

  test('can enter goal text', async ({ page }) => {
    await page.goto('/auto-planner');
    const textarea = page.getByTestId('auto-planner-goal-input');
    await textarea.fill('Implementiere User-Login mit JWT');
    await expect(textarea).toHaveValue('Implementiere User-Login mit JWT');
  });

  test('plan button is disabled without goal', async ({ page }) => {
    await page.goto('/auto-planner');
    const button = page.getByTestId('auto-planner-goal-plan');
    await expect(button).toBeDisabled();
  });

  test('plan button enables with goal text', async ({ page }) => {
    await page.goto('/auto-planner');
    await page.getByTestId('auto-planner-goal-input').fill('Test goal');
    const button = page.getByTestId('auto-planner-goal-plan');
    await expect(button).toBeEnabled();
  });

  test('shows advanced goal fields on demand', async ({ page }) => {
    await page.goto('/auto-planner');
    await expect(page.getByTestId('goal-advanced-fields')).toHaveCount(0);
    await page.getByTestId('goal-mode-toggle').click();
    await expect(page.getByTestId('goal-advanced-fields')).toBeVisible();
  });

  test('renders goal detail drilldown panels', async ({ page }) => {
    await mockJson(page, '**/tasks/auto-planner/status', { enabled: true, stats: { goals_processed: 1, tasks_created: 3, followups_created: 0 } });
    await mockJson(page, '**/teams', []);
    await mockJson(page, '**/goals', [
      { id: 'goal-1', summary: 'Ship release', status: 'planned', goal: 'Ship release' }
    ]);
    await mockJson(page, '**/goals/goal-1/detail', {
      goal: { id: 'goal-1', summary: 'Ship release', status: 'planned' },
      trace: { trace_id: 'goal-trace-1' },
      artifacts: {
        result_summary: { completed_tasks: 1, failed_tasks: 0 },
        headline_artifact: { preview: 'Release notes generated' }
      },
      plan: {
        plan: { id: 'plan-1' },
        nodes: [{ id: 'node-1', title: 'Draft notes', status: 'draft', priority: 'Medium', node_key: 'plan-1-node-1' }]
      },
      governance: {
        policy: { total: 1, approved: 1, blocked: 0 },
        verification: { total: 1, passed: 1, escalated: 0 },
        summary: { governance_visible: true, detail_level: 'full' }
      },
      tasks: [{ id: 'task-1', title: 'Draft notes', status: 'completed', trace_id: 'goal-trace-1', verification_status: { status: 'passed' } }]
    });

    await page.goto('/auto-planner');
    await page.getByText('Ship release').first().click();
    await expect(page.getByTestId('goal-detail-panel')).toBeVisible();
    await expect(page.getByTestId('goal-artifact-summary')).toContainText('Release notes generated');
    await expect(page.getByTestId('goal-governance-panel')).toBeVisible();
    await expect(page.getByTestId('goal-trace-panel')).toBeVisible();
  });
});

test.describe('Webhooks', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[name="username"]', ADMIN_USERNAME);
    await page.fill('input[name="password"]', ADMIN_PASSWORD);
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
  });

  test('displays webhooks page', async ({ page }) => {
    await page.goto('/webhooks');
    await expect(page.locator('h3')).toContainText('Webhooks');
  });

  test('shows status cards', async ({ page }) => {
    await page.goto('/webhooks');
    await expect(page.getByRole('heading', { name: /Webhooks/ })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Webhook-URLs' })).toBeVisible();
  });

  test('shows webhook URLs', async ({ page }) => {
    await page.goto('/webhooks');
    await expect(page.getByTestId('webhooks-urls-title')).toBeVisible();
    await expect(page.getByTestId('webhooks-url-generic')).toBeVisible();
    await expect(page.getByTestId('webhooks-url-github')).toBeVisible();
  });

  test('shows test form', async ({ page }) => {
    await page.goto('/webhooks');
    await expect(page.getByTestId('webhooks-test-title')).toBeVisible();
  });

  test('can test webhook', async ({ page }) => {
    await mockJson(page, '**/triggers/test', { ok: true, would_create: 1, source: 'generic' });
    await page.goto('/webhooks');
    const testButton = page.getByTestId('webhooks-test-run');
    await expect(testButton).toBeEnabled();
    await testButton.click();
    await expect(testButton).toBeDisabled();
  });

  test('shows GitHub integration guide', async ({ page }) => {
    await page.goto('/webhooks');
    await expect(page.locator('text=GitHub Integration')).toBeVisible();
  });
});
