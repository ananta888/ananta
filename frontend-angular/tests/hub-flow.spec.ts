import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Hub Flow', () => {
  test('create task and execute via hub locally (no worker assignment)', async ({ page }) => {
    await login(page);
    await page.goto('/board');

    // Create a new task with unique name
    const taskName = `E2E Hub Task ${Date.now()}`;
    await page.getByPlaceholder('Task title').fill(taskName);
    await page.getByRole('button', { name: 'Anlegen' }).click();

    // Open the task detail
    const taskLink = page.getByRole('link', { name: taskName });
    await expect(taskLink).toBeVisible();
    await taskLink.click();
    await expect(page.getByRole('heading', { name: /Task\s/i })).toBeVisible();

    // Fill manual command and execute via hub (local)
    await page.getByRole('button', { name: 'Interaktion' }).click();
    await page.getByLabel(/Vorgeschlagener Befehl/i).fill('echo e2e-hub');
    await page.getByRole('button', { name: 'Ausführen' }).click();

    // Logs should list the executed command
    await page.getByRole('button', { name: 'Logs' }).click();
    await expect(page.getByText('echo e2e-hub')).toBeVisible();
  });
});
