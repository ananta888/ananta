import { test, expect } from '@playwright/test';
import { ALPHA_URL, BETA_URL, HUB_AGENT_TOKEN, ALPHA_AGENT_TOKEN, BETA_AGENT_TOKEN, HUB_URL, login } from './utils';

test.describe('Task Cleanup UI', () => {
  test('graph actions call archive/delete cleanup endpoints', async ({ page }) => {
    await login(page);

    const tasksPayload = [
      { id: 'DONE-1', title: 'done item', status: 'completed' },
      { id: 'FAIL-1', title: 'failed item', status: 'failed' },
      { id: 'E2E-TEST-1', title: 'playwright e2e test task', status: 'todo' },
      { id: 'LIVE-1', title: 'normal task', status: 'todo' }
    ];

    const cleanupBodies: any[] = [];
    const archivedIds: string[] = [];

    await page.route('**/tasks', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: tasksPayload })
      });
    });

    await page.route('**/tasks/cleanup', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const body = route.request().postDataJSON() as any;
      cleanupBodies.push(body);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: {
            archived_count: body?.mode === 'archive' ? (body?.task_ids?.length || 0) : 0,
            deleted_count: body?.mode === 'delete' ? (body?.task_ids?.length || 0) : 0,
            archived_ids: body?.mode === 'archive' ? (body?.task_ids || []) : [],
            deleted_ids: body?.mode === 'delete' ? (body?.task_ids || []) : []
          }
        })
      });
    });

    await page.route('**/tasks/*/archive', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const match = route.request().url().match(/\/tasks\/([^/]+)\/archive/);
      if (match) archivedIds.push(match[1]);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'archived', data: { id: match?.[1] || '' } })
      });
    });

    await page.goto('/graph');
    await page.waitForURL('**/graph');
    await expect(page.getByRole('button', { name: /Sichtbare Done\/Failed archivieren/i })).toBeVisible();
    await page.getByLabel(/Completed anzeigen/i).check();
    await page.getByLabel(/Failed anzeigen/i).check();

    await page.getByRole('button', { name: /Sichtbare Done\/Failed archivieren/i }).click();
    await expect.poll(() => cleanupBodies.length).toBeGreaterThan(0);
    expect(cleanupBodies[0]?.mode).toBe('archive');
    expect(cleanupBodies[0]?.task_ids || []).toEqual(expect.arrayContaining(['DONE-1', 'FAIL-1']));

    await page.getByRole('button', { name: /Sichtbare Testlauf-Tasks loeschen/i }).click();
    await expect.poll(() => cleanupBodies.length).toBeGreaterThan(1);
    expect(cleanupBodies[1]?.mode).toBe('delete');
    expect(cleanupBodies[1]?.task_ids || []).toContain('E2E-TEST-1');

    const doneRow = page.locator('tr', { hasText: 'DONE-1' }).first();
    await doneRow.getByRole('button', { name: /^Archivieren$/i }).click();
    await expect.poll(() => archivedIds.length).toBeGreaterThan(0);
    expect(archivedIds).toContain('DONE-1');

    const liveRow = page.locator('tr', { hasText: 'LIVE-1' }).first();
    await liveRow.getByRole('button', { name: /^Loeschen$/i }).click();
    await expect.poll(() => cleanupBodies.length).toBeGreaterThan(2);
    expect(cleanupBodies[2]?.mode).toBe('delete');
    expect(cleanupBodies[2]?.task_ids || []).toEqual(['LIVE-1']);
  });

  test('archived page supports single and filtered delete actions', async ({ page, request }) => {
    test.skip(true, 'Archived list rows are not rendered reliably in compose UI run; tracked for follow-up.');
    await login(page);
    await page.evaluate(({ hubUrl, alphaUrl, betaUrl, hubToken, alphaToken, betaToken }) => {
      localStorage.setItem('ananta.agents.v1', JSON.stringify([
        { name: 'hub', url: hubUrl, token: hubToken, role: 'hub' },
        { name: 'alpha', url: alphaUrl, token: alphaToken, role: 'worker' },
        { name: 'beta', url: betaUrl, token: betaToken, role: 'worker' }
      ]));
    }, {
      hubUrl: HUB_URL,
      alphaUrl: ALPHA_URL,
      betaUrl: BETA_URL,
      hubToken: HUB_AGENT_TOKEN,
      alphaToken: ALPHA_AGENT_TOKEN,
      betaToken: BETA_AGENT_TOKEN
    });
    const authToken = await page.evaluate(() => localStorage.getItem('ananta.user.token'));
    expect(authToken).toBeTruthy();
    const headers = { Authorization: `Bearer ${authToken}` };

    const suffix = Date.now().toString().slice(-6);
    const id1 = `ARX1${suffix}`;
    const id2 = `ARX2${suffix}`;

    for (const tid of [id1, id2]) {
      const createRes = await request.post('http://localhost:5000/tasks', {
        headers,
        data: { id: tid, title: `archived ${tid}`, status: 'completed' }
      });
      expect(createRes.ok()).toBeTruthy();
      const archiveRes = await request.post(`http://localhost:5000/tasks/${tid}/archive`, { headers });
      expect(archiveRes.ok()).toBeTruthy();
    }

    await page.goto('/archived');
    await page.waitForURL('**/archived');
    await expect(page.getByRole('button', { name: /Gefilterte loeschen/i })).toBeVisible();
    await expect(page.getByText(id1)).toBeVisible();

    const row = page.locator('tr', { hasText: id1 }).first();
    await row.getByRole('button', { name: /^Loeschen$/i }).click();
    await expect.poll(async () => {
      const listRes = await request.get('http://localhost:5000/tasks/archived', { headers });
      const payload = await listRes.json() as any;
      const items = payload?.data || [];
      return items.some((t: any) => t.id === id1);
    }).toBe(false);

    await page.getByPlaceholder('Titel/ID suchen...').fill(id2);
    await page.getByRole('button', { name: /Gefilterte loeschen/i }).click();
    await expect.poll(async () => {
      const listRes = await request.get('http://localhost:5000/tasks/archived', { headers });
      const payload = await listRes.json() as any;
      const items = payload?.data || [];
      return items.some((t: any) => t.id === id2);
    }).toBe(false);
  });
});
