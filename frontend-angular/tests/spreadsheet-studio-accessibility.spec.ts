import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { gotoProjectScopedRoute, loginFast } from './utils';

const LIVE = process.env['RUN_SPREADSHEET_STUDIO_A11Y_E2E'] === '1';
const digest = 'a'.repeat(64);
const document = {
  schema: 'ananta.spreadsheet-document-version.v1',
  document_id: 'document-browser-gate',
  title: 'Accessible Budget',
  version: 1,
  snapshot: {
    schema: 'ananta.spreadsheet-workbook-snapshot.v1',
    snapshot_id: 'snapshot-browser-gate',
    document_version_id: 'version-browser-gate',
    sheets: [
      { sheet_id: 'sheet-visible', name: 'Budget', hidden: false, cells: [] },
      { sheet_id: 'sheet-hidden', name: 'Archive', hidden: true, cells: [] },
    ],
  },
  snapshot_digest: digest,
  state: 'published',
  unsupported_objects: ['embedded_object'],
  source_grounding_verified: false,
  human_intervention_required: false,
};

test.describe('Spreadsheet Studio real-browser accessibility gate', () => {
  test.skip(!LIVE, 'release evidence requires an automatic real-browser run');

  test('virtualized workbook remains keyboard-readable and blocks unsafe apply', async ({ page, request }) => {
    test.setTimeout(180_000);
    await loginFast(page, request);
    await page.route('**/api/spreadsheet-studio/**', async route => {
      const url = new URL(route.request().url());
      let data: unknown;
      if (url.pathname.endsWith('/capabilities')) {
        data = {
          schema: 'ananta.spreadsheet-studio-capability.v1', available: true, state: 'available',
          mode: 'worker', automatic_promotion_enabled: true, supported_formats: ['xlsx', 'ods', 'csv'],
          libreoffice_fidelity_verified: true, training_available: true, source_grounding_verified: false,
          executor: { state: 'available' }, human_intervention_required: false,
        };
      } else if (url.pathname.endsWith('/documents')) {
        data = { items: [document], limit: 100 };
      } else if (url.pathname.endsWith('/versions')) {
        data = { items: [document], limit: 100 };
      } else if (url.pathname.endsWith('/viewport')) {
        data = {
          schema: 'ananta.spreadsheet-workbook-viewport.v1', snapshot_digest: digest,
          sheet_id: url.searchParams.get('sheet_id'), range: { start: 'A1', end: 'Z100' },
          tile: { row: 1, column: 1, rows: 100, columns: 26 }, offset: 0, limit: 250,
          total: 2, has_more: false,
          cells: [
            { address: 'A1', value: 41, displayed_value: '41', formula: null, style_ref: null },
            { address: 'B1', value: 42, displayed_value: '42', formula: { op: 'add' }, formula_text: '=A1+1', style_ref: null },
          ],
          projection_digest: 'b'.repeat(64), backend_cell_count: 2,
          source_grounding_verified: false, human_intervention_required: false,
        };
      } else {
        return route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ status: 'error' }) });
      }
      return route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ status: 'success', data }) });
    });

    await gotoProjectScopedRoute(page, '/spreadsheet-studio');
    await page.getByRole('button', { name: /Accessible Budget/ }).click();
    const viewer = page.locator('app-spreadsheet-workbook-viewer');
    await expect(viewer.getByRole('grid', { name: 'Workbook-Zellen' })).toBeVisible();
    await expect(viewer.getByRole('row')).toHaveCount(2);
    await expect(viewer.getByText(/Archive.*verborgen/)).toBeVisible();
    await expect(page.getByRole('button', { name: 'Candidate erzeugen' })).toBeDisabled();

    await viewer.getByRole('row').first().focus();
    await expect(viewer.getByRole('row').first()).toBeFocused();
    const aria = await viewer.ariaSnapshot();
    expect(aria).toContain('grid "Workbook-Zellen"');
    expect(aria).toContain('A1, Wert 41');
    const axe = await new AxeBuilder({ page })
      .include('app-spreadsheet-studio-page')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    expect(axe.violations).toEqual([]);
  });
});
