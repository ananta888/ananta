import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Agent Registration', () => {
  test('shows registered worker in dashboard', async ({ page }) => {
    await page.route('http://localhost:5000/agents', async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          'worker-echo': {
            status: 'online',
            role: 'worker',
            resources: { cpu_percent: 1, ram_bytes: 1048576 }
          }
        })
      });
    });

    await login(page);
    
    // Wait for agents API call to complete
    const agentsPromise = page.waitForResponse(res => res.url().includes('/agents') && res.request().method() === 'GET');
    await page.goto('/dashboard');
    await agentsPromise;

    await expect(page.getByText('Agenten Status')).toBeVisible();
    await expect(page.getByText('worker-echo')).toBeVisible();
  });
});
