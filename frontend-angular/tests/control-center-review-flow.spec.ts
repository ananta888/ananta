import { expect, test } from '@playwright/test';
import { loginFast } from './utils';

test.describe('Control Center review flow', () => {
  test('opens artifacts area and shows verification linkage', async ({ page, request }) => {
    await loginFast(page, request);

    await page.route('**/api/projects', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { items: [{ id: 'proj-1', name: 'Project 1' }], count: 1 } }),
      });
    });
    await page.route('**/api/projects/proj-1/tasks', async (route) => {
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ status: 'success', data: { items: [], count: 0 } }),
      });
    });
    await page.route('**/artifacts*', async (route) => {
      const url = route.request().url();
      if (route.request().resourceType() === 'document') {
        await route.continue();
        return;
      }
      if (url.includes('/artifacts/art-report-1/content')) {
        const payload = Buffer.from('# Verification Report\n\n- passed: 5\n- failed: 0').toString('base64');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'success', data: { type: 'text/markdown', encoding: 'base64', payload, has_more: false, next_offset: null } }),
        });
        return;
      }
      if (url.includes('/artifacts/art-diff-1/content')) {
        const payload = Buffer.from('--- a/a.ts\n+++ b/a.ts\n@@\n-const x=1\n+const x=2').toString('base64');
        await route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify({ status: 'success', data: { type: 'text/x-diff', encoding: 'base64', payload, has_more: false, next_offset: null } }),
        });
        return;
      }
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          status: 'success',
          data: [
            { id: 'art-report-1', latest_filename: 'verification-report.md', latest_media_type: 'text/markdown' },
            { id: 'art-diff-1', latest_filename: 'review.diff', latest_media_type: 'text/x-diff' },
          ],
        }),
      });
    });

    await page.goto('/control-center/artifacts', { waitUntil: 'domcontentloaded' });
    await expect(page).toHaveURL(/\/control-center\/artifacts/);
    await expect(page.getByRole('button', { name: /verification-report\.md/i })).toBeVisible();

    await page.getByRole('button', { name: /review.diff/i }).click();
    await expect(page.getByText('Unified')).toBeVisible();
  });
});
