import { test, expect, type APIRequestContext, type Page } from '@playwright/test';
import {
  HUB_URL,
  loginFast,
  assertErrorOverlaysInViewport,
  assertNoUnhandledBrowserErrors,
} from './utils';

type HubInfo = { hubUrl: string; token: string | null };

function unwrap<T = any>(body: any): T {
  if (body && typeof body === 'object' && 'data' in body) return body.data as T;
  return body as T;
}

async function getHubInfo(page: Page): Promise<HubInfo> {
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

async function apiCall(
  request: APIRequestContext,
  method: 'GET' | 'POST' | 'PATCH' | 'DELETE',
  url: string,
  token: string | null,
  data?: any,
) {
  const headers = token ? { Authorization: `Bearer ${token}` } : undefined;
  if (method === 'GET') return request.get(url, { headers });
  if (method === 'POST') return request.post(url, { headers, data });
  if (method === 'PATCH') return request.patch(url, { headers, data });
  return request.delete(url, { headers, data });
}

test.describe('Main Goal Observability Journey', () => {
  test('keeps board/task detail/log tabs analysable and controllable', async ({ page, request }) => {
    test.setTimeout(90_000);
    await loginFast(page, request);
    const { hubUrl, token } = await getHubInfo(page);
    expect(token).toBeTruthy();
    const authToken = token as string;

    const title = `E2E Observe Task ${Date.now()}`;
    let createdTaskId: string | null = null;

    await page.route('**/tasks/*/stream-logs**', async (route) => {
      const body = [
        'data: {"event_type":"task_started","output":"execution started"}',
        '',
        'data: {"command":"echo observability","output":"ok","exit_code":0}',
        '',
      ].join('\n');
      await route.fulfill({
        status: 200,
        headers: {
          'content-type': 'text/event-stream',
          'cache-control': 'no-cache',
          connection: 'keep-alive',
          'access-control-allow-origin': '*',
        },
        body,
      });
    });

    try {
      const createRes = await apiCall(request, 'POST', `${hubUrl}/tasks`, authToken, {
        title,
        description: 'observability coverage for board and task detail',
        status: 'todo',
      });
      expect(createRes.ok()).toBeTruthy();
      const created = unwrap<any>(await createRes.json());
      createdTaskId = created?.id || null;
      expect(createdTaskId).toBeTruthy();

      await page.goto('/board', { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: /^Board$/i })).toBeVisible();
      await expect(page.getByRole('button', { name: /Sprint Board Ansicht/i })).toBeVisible();
      await expect(page.getByRole('button', { name: /Scrum Insights Ansicht/i })).toBeVisible();

      await page.getByRole('button', { name: /Scrum Insights Ansicht/i }).click();
      await expect(page.getByRole('heading', { name: /Burndown Chart/i })).toBeVisible();
      await page.getByRole('button', { name: /Sprint Board Ansicht/i }).click();
      await expect(page.getByPlaceholder('Suchen...')).toBeVisible();

      await page.getByPlaceholder('Suchen...').fill(title);
      const detailLink = page.getByRole('link', { name: new RegExp(`Task Details für ${title}`) });
      await expect(detailLink.first()).toBeVisible();
      await detailLink.first().click();

      await expect(page.getByRole('heading', { name: /Task #/i })).toBeVisible();
      await page.getByRole('button', { name: /^Logs$/i }).click();
      await expect(page.getByRole('heading', { name: /Task Logs \(Live\)/i })).toBeVisible();
      await page.waitForTimeout(1200);
      const logCount = await page.locator('.log-entry').count();
      if (logCount > 0) {
        await expect(page.locator('.log-entry-code', { hasText: 'echo observability' })).toBeVisible();
      } else {
        await expect(page.getByText(/Bisher wurden keine Aktionen/i)).toBeVisible();
      }

      await page.getByRole('button', { name: /^Details$/i }).click();
      await expect(page.locator('label:has-text("Status") select').first()).toBeVisible();

      await assertErrorOverlaysInViewport(page);
      await assertNoUnhandledBrowserErrors(page);
    } finally {
      if (createdTaskId) {
        await apiCall(request, 'POST', `${hubUrl}/tasks/cleanup`, authToken, { mode: 'delete', task_ids: [createdTaskId] });
      }
      if (!page.isClosed()) {
        await page.unroute('**/tasks/*/stream-logs**');
      }
    }
  });

  test('covers artifacts controls with deterministic API interactions', async ({ page, request }) => {
    test.setTimeout(90_000);
    await loginFast(page, request);
    await getHubInfo(page);

    const uploadCalls: any[] = [];
    const collectionIndexCalls: string[] = [];
    const collectionDetailId = 'COLL-1';
    const artifactId = 'ART-1';

    await page.route('**/*', async (route) => {
      const req = route.request();
      const url = new URL(req.url());
      const path = url.pathname;
      const method = req.method();
      const isDocument = req.resourceType() === 'document';

      if (path === '/knowledge/index-profiles' && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'success',
            data: {
              items: [{ name: 'default', label: 'Default', description: 'E2E profile', is_default: true }],
            },
          }),
        });
        return;
      }

      if (path === '/knowledge/collections' && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'success',
            data: [{ id: collectionDetailId, name: 'e2e-collection', description: 'seeded collection', created_by: 'e2e' }],
          }),
        });
        return;
      }

      if (path === `/knowledge/collections/${collectionDetailId}` && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'success',
            data: { id: collectionDetailId, name: 'e2e-collection', description: 'seeded collection', entry_count: 1 },
          }),
        });
        return;
      }

      if (path === '/artifacts' && method === 'GET' && !isDocument) {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'success',
            data: [{ id: artifactId, latest_filename: 'e2e-note.txt', status: 'ready', latest_media_type: 'text/plain', size_bytes: 18, created_by: 'e2e' }],
          }),
        });
        return;
      }

      if (path === `/artifacts/${artifactId}` && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'success',
            data: {
              artifact: { id: artifactId, latest_filename: 'e2e-note.txt', status: 'ready', latest_media_type: 'text/plain', size_bytes: 18 },
              versions: [{ id: 'VER-1', version_number: 1, original_filename: 'e2e-note.txt', media_type: 'text/plain', sha256: 'abc123' }],
              extracted_documents: [{ id: 'DOC-1', extraction_mode: 'text', extraction_status: 'completed', text_content: 'hello artifact world' }],
              knowledge_links: [{ collection_id: collectionDetailId, link_metadata: { collection_name: 'e2e-collection' } }],
              knowledge_index: { status: 'indexed', profile_name: 'default' },
            },
          }),
        });
        return;
      }

      if (path === `/artifacts/${artifactId}/rag-status` && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'success', data: { knowledge_index: { status: 'indexed', profile_name: 'default' } } }),
        });
        return;
      }

      if (path === `/artifacts/${artifactId}/rag-preview` && method === 'GET') {
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({
            status: 'success',
            data: {
              knowledge_index: { profile_name: 'default' },
              manifest: { file_count: 1, index_record_count: 1, detail_record_count: 1, relation_record_count: 1 },
              preview: { index: [{ title: 'e2e-note', file: 'e2e-note.txt' }] },
            },
          }),
        });
        return;
      }

      if (path === '/artifacts/upload' && method === 'POST') {
        uploadCalls.push({ path, method });
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'success', data: { artifact: { id: artifactId } } }),
        });
        return;
      }

      if (path === `/knowledge/collections/${collectionDetailId}/index` && method === 'POST') {
        collectionIndexCalls.push(collectionDetailId);
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'success', data: { collection_id: collectionDetailId } }),
        });
        return;
      }

      await route.continue();
    });

    try {
      await page.goto('/artifacts', { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: /Artifacts & Knowledge/i })).toBeVisible();
      await expect(page.getByTestId('artifact-upload-btn')).toBeVisible();

      await page.getByTestId('artifact-collection-input').fill('e2e-collection');
      await page.getByTestId('artifact-file-input').setInputFiles({
        name: 'e2e-note.txt',
        mimeType: 'text/plain',
        buffer: Buffer.from('hello artifact world'),
      });

      await page.getByTestId('artifact-upload-btn').click();
      await expect.poll(() => uploadCalls.length).toBeGreaterThan(0);
      await expect(page.getByRole('button', { name: /Collection indexieren/i })).toBeEnabled();
      await page.getByRole('button', { name: /Collection indexieren/i }).click();
      await expect.poll(() => collectionIndexCalls.length).toBeGreaterThan(0);

      await assertErrorOverlaysInViewport(page);
      await assertNoUnhandledBrowserErrors(page);
    } finally {
      if (!page.isClosed()) {
        await page.unroute('**/*');
      }
    }
  });
});
