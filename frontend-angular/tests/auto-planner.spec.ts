import { test, expect } from '@playwright/test';
import { mockJson } from './helpers/mock-http';

test.describe('Auto-Planner', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin');
    await page.click('button[type="submit"]');
    await page.waitForURL('**/dashboard');
  });

  test('displays auto-planner page', async ({ page }) => {
    await page.goto('/auto-planner');
    await expect(page.locator('h3')).toContainText('Auto-Planner');
  });

  test('shows status cards', async ({ page }) => {
    await page.goto('/auto-planner');
    await expect(page.getByRole('heading', { name: 'Auto-Planner' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Konfiguration' })).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Neues Goal planen' })).toBeVisible();
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
});

test.describe('Webhooks', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin');
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
