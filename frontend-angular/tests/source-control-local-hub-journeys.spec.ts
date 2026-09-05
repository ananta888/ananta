import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import {
  LOCAL_SOURCE_CONTROL_IDS,
  startSourceControlLocalHub,
} from './helpers/source-control-local-hub';
import type { SourceControlLocalHub } from './helpers/source-control-local-hub';
import { gotoProjectScopedRoute, loginFast } from './utils';

test.describe('Source Control v1 against the deterministic local Hub', () => {
  let hub: SourceControlLocalHub;

  test.beforeEach(async ({ page, request }) => {
    hub = await startSourceControlLocalHub();
    await page.route('**://github.com/**', (route) => route.abort('blockedbyclient'));
    await page.route('**://api.github.com/**', (route) => route.abort('blockedbyclient'));
    await hub.install(page);
    await loginFast(page, request);
  });

  test.afterEach(async () => {
    await hub.close();
  });

  test('declares deterministic evidence without claiming production capability', async ({
    request,
  }) => {
    const response = await request.get(`${hub.origin}/api/source-control/v1/__test-support`);
    expect(response.ok()).toBeTruthy();
    const payload = await response.json();
    expect(payload.data).toEqual({
      contract: 'local-deterministic-hub',
      deterministic: true,
      production_capability: false,
    });
  });

  test('admits DirectText and Notebook through validate-then-create', async ({ page }) => {
    await gotoProjectScopedRoute(page, '/sources/add');
    await page.getByTestId('direct-display-name').fill('Architecture notes');
    await page.getByTestId('direct-content').fill('# Hub-owned source');
    await page.getByTestId('submit-source').click();
    await expect(page.getByTestId('content-admission-success')).toBeVisible();

    await gotoProjectScopedRoute(page, '/sources/add');
    await page.getByRole('button', { name: /Notebook/ }).click();
    await page.getByTestId('notebook-display-name').fill('Runbook');
    await page.getByTestId('notebook-json').fill(
      JSON.stringify({
        cells: [
          {
            cell_type: 'code',
            source: 'print("ok")',
            outputs: [{ output_type: 'stream', text: 'ok' }],
          },
        ],
      }),
    );
    await page.getByTestId('submit-source').click();
    await expect(page.getByTestId('content-admission-success')).toBeVisible();

    const writes = hub.operations.filter(
      (operation) =>
        operation.path === '/api/source-control/v1/content-admissions' ||
        operation.path === '/api/source-control/v1/content-admissions/validate',
    );
    expect(writes.map((operation) => operation.path)).toEqual([
      '/api/source-control/v1/content-admissions/validate',
      '/api/source-control/v1/content-admissions',
      '/api/source-control/v1/content-admissions/validate',
      '/api/source-control/v1/content-admissions',
    ]);
  });

  test('binds a registered workspace without browser identity material', async ({ page }) => {
    await gotoProjectScopedRoute(page, '/sources/add');
    await page.getByRole('button', { name: /Registrierter Workspace/ }).click();
    await page.getByTestId('workspace-display-name').fill('Primary workspace');
    await page.getByTestId('workspace-catalog').selectOption('workspace-primary');
    await page.getByTestId('submit-source').click();
    await expect(page.getByTestId('content-admission-success')).toBeVisible();

    const requests = hub.operations.filter(
      (operation) =>
        (operation.method === 'POST' &&
          operation.path === '/api/source-control/v1/connections/validate') ||
        (operation.method === 'POST' && operation.path === '/api/source-control/v1/connections'),
    );
    expect(requests).toHaveLength(2);
    expect(requests[0].body).toMatchObject({
      connector_type: 'registered_workspace',
      workspace_id: 'workspace-primary',
      display_name: 'Primary workspace',
      dry_run: true,
    });
    expect(JSON.stringify(requests)).not.toContain('connection_identity_digest');
    expect(JSON.stringify(requests)).not.toContain('workspace_path');
    expect(JSON.stringify(requests)).not.toContain('repository_url');
  });

  test('executes index and grant lifecycle with Hub IDs, ETags and idempotency', async ({
    page,
  }) => {
    await gotoProjectScopedRoute(page, `/sources/${LOCAL_SOURCE_CONTROL_IDS.connectionId}`);
    await page.getByRole('tab', { name: 'Runs' }).click();
    await page.getByTestId('index-profile').selectOption('profile-default');
    await page.getByTestId('index-start').click();
    await expect(page.getByText('Indexlauf wurde serverseitig gestartet.')).toBeVisible();
    await page.getByTestId('index-activate').first().click();
    await expect(page.getByText('Index wurde serverseitig aktiviert.')).toBeVisible();
    await page.getByTestId('index-rollback').first().click();
    await expect(page.getByText('Rollback wurde serverseitig angefordert.')).toBeVisible();

    await page.getByRole('tab', { name: 'Zugriff' }).click();
    await page.getByTestId('grant-destination').fill('hub-destination-primary');
    await page.getByTestId('grant-policy').fill('policy-primary');
    await page.getByTestId('grant-policy-etag').fill('e'.repeat(64));
    await page.getByTestId('grant-preset').selectOption('preset-read');
    await page.getByTestId('grant-duration').fill('900');
    await page.getByTestId('grant-create').click();
    await expect(page.getByText('Grant wurde serverseitig ausgestellt.')).toBeVisible();
    await page.getByTestId('grant-revoke').first().click();
    await expect(page.getByText('Grant wurde serverseitig widerrufen.')).toBeVisible();

    const guardedWrites = hub.operations.filter(
      (operation) =>
        operation.method === 'POST' &&
        (operation.path.includes('/runs') ||
          operation.path.includes('/indices/') ||
          operation.path.includes('/grants')),
    );
    expect(guardedWrites.length).toBeGreaterThanOrEqual(5);
    for (const operation of guardedWrites) {
      expect(operation.headers['if-match']).toBeTruthy();
      expect(operation.headers['idempotency-key']).toMatch(/^ui:/);
    }
  });

  test('activates and rolls back Context Access Policy versions through Angular', async ({
    page,
  }) => {
    await gotoProjectScopedRoute(page, '/context-access-policy');
    const policyRow = page.locator('tr').filter({ hasText: 'policy-primary' }).first();
    await expect(policyRow).toBeVisible();
    await policyRow.getByRole('button').first().click();

    const draftRow = page
      .locator('tr')
      .filter({ hasText: /policy-primary|Version 2|draft/i })
      .filter({ hasText: /2/ })
      .first();
    await expect(draftRow).toBeVisible();
    await draftRow.getByRole('button').first().click();
    await page.getByRole('button', { name: /Aktivieren/i }).click();

    const rollbackTarget = page.getByLabel(/Zielversion|Rollback.*Version/i);
    if ((await rollbackTarget.evaluate((element) => element.tagName)) === 'SELECT') {
      await rollbackTarget.selectOption('1');
    } else {
      await rollbackTarget.fill('1');
    }
    await page.getByRole('button', { name: /Rollback/i }).click();

    await expect
      .poll(() =>
        hub.operations
          .filter(
            (operation) =>
              operation.method === 'POST' &&
              operation.path.includes('/context-policies/policy-primary/'),
          )
          .map((operation) => operation.path),
      )
      .toEqual([
        '/api/source-control/v1/context-policies/policy-primary/versions/2/activate',
        '/api/source-control/v1/context-policies/policy-primary/rollback',
      ]);
    const transitions = hub.operations.filter(
      (operation) =>
        operation.method === 'POST' && operation.path.includes('/context-policies/policy-primary/'),
    );
    for (const operation of transitions) {
      expect(operation.headers['if-match']).toBeTruthy();
      expect(operation.headers['idempotency-key']).toBeTruthy();
      expect(operation.body).toMatchObject({ dry_run: false });
    }
  });

  test('meets the source-control keyboard, focus and automated axe gate', async ({ page }) => {
    await gotoProjectScopedRoute(page, '/sources/add');
    const directText = page.getByRole('button', { name: /Direkttext/ });
    const notebook = page.getByRole('button', { name: /Notebook/ });
    await expect(directText).toBeVisible();
    await directText.focus();
    await expect(directText).toBeFocused();
    await page.keyboard.press('Tab');
    await expect(notebook).toBeFocused();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('heading', { name: 'Notebook' })).toBeVisible();

    const results = await new AxeBuilder({ page })
      .include('main')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa'])
      .analyze();
    expect(results.violations).toEqual([]);

    const contrastRatio = await page.locator('.source-card.selected').evaluate((element) => {
      const channels = (value: string): number[] =>
        (value.match(/\d+(?:\.\d+)?/g) ?? []).slice(0, 3).map(Number);
      const luminance = (rgb: number[]): number => {
        const linear = rgb.map((channel) => {
          const normalized = channel / 255;
          return normalized <= 0.03928 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
        });
        return 0.2126 * (linear[0] ?? 0) + 0.7152 * (linear[1] ?? 0) + 0.0722 * (linear[2] ?? 0);
      };
      const style = getComputedStyle(element);
      const foreground = luminance(channels(style.color));
      const background = luminance(channels(style.backgroundColor));
      return (Math.max(foreground, background) + 0.05) / (Math.min(foreground, background) + 0.05);
    });
    expect(contrastRatio).toBeGreaterThanOrEqual(4.5);
  });
});
