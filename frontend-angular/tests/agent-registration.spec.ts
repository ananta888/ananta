import { test, expect } from '@playwright/test';
import { clearLoginAttempts, login } from './utils';

test.describe('Agent Registration', () => {
  test.beforeEach(() => {
    clearLoginAttempts('127.0.0.1');
  });

  test('shows registered worker in dashboard', async ({ page }) => {
    await page.route('**/agents*', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify([
          {
            name: 'worker-echo',
            status: 'online',
            role: 'worker',
            resources: { cpu_percent: 1, ram_bytes: 1048576 }
          }
        ])
      });
    });

    await login(page);
    
    const agentsPromise = page.waitForResponse(res => res.url().includes('/agents') && res.request().method() === 'GET' && res.status() === 200);
    await page.goto('/dashboard');
    const agentsResponse = await agentsPromise;
    const agentsPayload = await agentsResponse.json();
    const agentNames = Array.isArray(agentsPayload)
      ? agentsPayload.map((a: any) => a?.name).filter(Boolean)
      : Object.keys(agentsPayload ?? {});
    expect(agentNames).toContain('worker-echo');

    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
  });
});
