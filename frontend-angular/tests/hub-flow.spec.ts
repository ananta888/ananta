import { test, expect } from '@playwright/test';

test.describe('Hub Flow', () => {
  test('create task and execute via hub locally (no worker assignment)', async ({ page }) => {
    await page.goto('/board');

    // Create a new task
    await page.getByPlaceholder('Task title').fill('E2E Hub Task');
    await page.getByTestId('btn-create-task').click();

    // Open the task detail
    await page.getByRole('link', { name: 'E2E Hub Task' }).click();
    await expect(page.getByRole('heading', { name: /Task\s/i })).toBeVisible();

    // Fill manual command and execute via hub (local)
    await page.getByPlaceholder('z. B. echo hello').fill('echo e2e-hub');
    await page.getByRole('button', { name: 'Ausführen' }).click();

    // Logs should list the executed command
    await expect(page.getByText('echo e2e-hub')).toBeVisible();
  });
});
