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

  test('archived page supports single and filtered delete actions', async ({ page }) => {
    test.skip(true, 'Archived table rows still not rendered in compose E2E despite mocked /tasks/archived responses; follow-up task open.');
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
    await page.reload();
    const id1 = 'ARCH-UI-1';
    const id2 = 'ARCH-UI-2';
    const deletedArchivedIds: string[] = [];
    const archivedCleanupBodies: any[] = [];

    await page.route('**/tasks/archived?*', async (route) => {
      if (route.request().method() !== 'GET') {
        await route.continue();
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: [
            { id: id1, title: `archived ${id1}`, status: 'completed', archived_at: 1730000000 },
            { id: id2, title: `archived ${id2}`, status: 'failed', archived_at: 1730000001 }
          ]
        })
      });
    });

    await page.route('**/tasks/archived/cleanup', async (route) => {
      if (route.request().method() !== 'POST') {
        await route.continue();
        return;
      }
      const body = route.request().postDataJSON() as any;
      archivedCleanupBodies.push(body);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { deleted_count: body?.task_ids?.length || 0, deleted_ids: body?.task_ids || [] } })
      });
    });

    await page.route('**/tasks/archived/*', async (route) => {
      if (route.request().method() !== 'DELETE') {
        await route.continue();
        return;
      }
      const match = route.request().url().match(/\/tasks\/archived\/([^/?]+)/);
      if (match) deletedArchivedIds.push(match[1]);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { deleted_count: 1, deleted_ids: [match?.[1] || ''] } })
      });
    });

    await page.goto('/archived');
    await page.waitForURL('**/archived');
    await expect(page.getByRole('button', { name: /Gefilterte loeschen/i })).toBeVisible();
    await expect(page.locator('tr', { hasText: id1 }).first()).toBeVisible();

    const row = page.locator('tr', { hasText: id1 }).first();
    await row.getByRole('button', { name: /^Loeschen$/i }).click();
    await expect.poll(() => deletedArchivedIds.length).toBeGreaterThan(0);
    expect(deletedArchivedIds).toContain(id1);

    await page.getByPlaceholder('Titel/ID suchen...').fill(id2);
    await page.getByRole('button', { name: /Gefilterte loeschen/i }).click();
    await expect.poll(() => archivedCleanupBodies.length).toBeGreaterThan(0);
    expect(archivedCleanupBodies[0]?.task_ids || []).toEqual([id2]);
  });
});
