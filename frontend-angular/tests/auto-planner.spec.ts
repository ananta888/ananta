import { test, expect } from '@playwright/test';

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
    await expect(page.locator('text=Konfiguration')).toBeVisible();
    await expect(page.locator('input[type="checkbox"]')).toHaveCount(3);
  });

  test('has goal input form', async ({ page }) => {
    await page.goto('/auto-planner');
    await expect(page.locator('text=Neues Goal planen')).toBeVisible();
    await expect(page.locator('textarea')).toBeVisible();
  });

  test('can enter goal text', async ({ page }) => {
    await page.goto('/auto-planner');
    const textarea = page.locator('textarea');
    await textarea.fill('Implementiere User-Login mit JWT');
    await expect(textarea).toHaveValue('Implementiere User-Login mit JWT');
  });

  test('plan button is disabled without goal', async ({ page }) => {
    await page.goto('/auto-planner');
    const button = page.locator('button:has-text("Goal planen")');
    await expect(button).toBeDisabled();
  });

  test('plan button enables with goal text', async ({ page }) => {
    await page.goto('/auto-planner');
    await page.locator('textarea').fill('Test goal');
    const button = page.locator('button:has-text("Goal planen")');
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
    await expect(page.locator('text=Webhook-URLs')).toBeVisible();
    await expect(page.locator('text=Generic')).toBeVisible();
    await expect(page.locator('text=GitHub')).toBeVisible();
  });

  test('shows test form', async ({ page }) => {
    await page.goto('/webhooks');
    await expect(page.locator('text=Webhook testen')).toBeVisible();
  });

  test('can test webhook', async ({ page }) => {
    await page.goto('/webhooks');
    await page.click('button:has-text("Testen")');
    await expect(page.locator('text=Ergebnis')).toBeVisible({ timeout: 10000 });
  });

  test('shows GitHub integration guide', async ({ page }) => {
    await page.goto('/webhooks');
    await expect(page.locator('text=GitHub Integration')).toBeVisible();
  });
});
