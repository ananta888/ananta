import { test, expect } from '@playwright/test';
import { HUB_URL, login } from './utils';

test.describe('Hub Flow', () => {
  async function getHubInfo(page: any) {
    return page.evaluate((defaultHubUrl: string) => {
      const token = localStorage.getItem('ananta.user.token');
      const raw = localStorage.getItem('ananta.agents.v1');
      let hubUrl = defaultHubUrl;
      if (raw) {
        try {
          const agents = JSON.parse(raw);
          const hub = agents.find((a: any) => a.role === 'hub');
          if (hub?.url) hubUrl = hub.url;
        } catch {}
      }
      if (!hubUrl || hubUrl === 'undefined') hubUrl = defaultHubUrl;
      return { hubUrl, token };
    }, HUB_URL);
  }

  test('create task and execute via hub locally (no worker assignment)', async ({ page, request }) => {
    await login(page);
    const { hubUrl, token } = await getHubInfo(page);
    const headers = token ? { Authorization: `Bearer ${token}` } : undefined;

    const taskName = `E2E Hub Task ${Date.now()}`;
    const createRes = await request.post(`${hubUrl}/tasks`, {
      headers,
      data: { title: taskName, status: 'backlog' }
    });
    expect(createRes.ok()).toBeTruthy();
    const createBody = await createRes.json();
    const created = createBody?.data || createBody;
    const taskId = created?.id;
    expect(taskId).toBeTruthy();

    await page.route(`**/tasks/${taskId}/step/execute*`, async route => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ output: 'e2e-hub', exit_code: 0 })
      });
    });

    await page.goto(`/task/${taskId}`);
    await expect(page.getByRole('heading', { name: /Task/i })).toBeVisible();

    await page.getByRole('button', { name: /Interaktion/i }).click();
    await page.getByLabel(/Vorgeschlagener Befehl/i).fill('echo e2e-hub');

    const executePromise = page.waitForResponse(res =>
      res.url().includes(`/tasks/${taskId}/step/execute`) &&
      res.request().method() === 'POST' &&
      res.status() === 200
    );
    await page.getByRole('button', { name: /Ausf/i }).click();
    await executePromise;

    await expect(page.getByText('Exit:')).toBeVisible();
    await expect(page.locator('pre').first()).toContainText('e2e-hub');
  });
});
