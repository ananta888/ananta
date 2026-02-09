import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Hub Flow', () => {
  test('create task and execute via hub locally (no worker assignment)', async ({ page }) => {
    await login(page);
    
    // Wait for tasks to load
    const tasksPromise = page.waitForResponse(res => res.url().includes('/tasks') && res.request().method() === 'GET');
    await page.goto('/board');
    await tasksPromise;

    // Create a new task with unique name
    const taskName = `E2E Hub Task ${Date.now()}`;
    await page.getByPlaceholder('Task title').fill(taskName);
    
    // Wait for task creation
    const createPromise = page.waitForResponse(res => res.url().includes('/tasks') && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Anlegen' }).click();
    await createPromise;

    // Open the task detail
    const taskLink = page.getByRole('link', { name: taskName });
    await expect(taskLink).toBeVisible();
    await taskLink.click();
    await expect(page.getByRole('heading', { name: /Task\s/i })).toBeVisible();

    // Fill manual command and execute via hub (local)
    await page.getByRole('button', { name: 'Interaktion' }).click();
    await page.getByLabel(/Vorgeschlagener Befehl/i).fill('echo e2e-hub');
    
    // Wait for command execution
    const executePromise = page.waitForResponse(res => res.url().includes('/step/execute') && res.request().method() === 'POST');
    await page.getByRole('button', { name: 'Ausführen' }).click();
    await executePromise;

    // Logs should list the executed command
    await page.getByRole('button', { name: 'Logs' }).click();
    await expect(page.getByText('echo e2e-hub')).toBeVisible();
  });
});
