import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { gotoProjectScopedRoute, loginFast } from './utils';

test.describe('GeoMap Studio', () => {
  test('CSV to offline map is keyboard-operable and policy-gated without human input', async ({ page, request }) => {
    test.setTimeout(120_000);
    await loginFast(page, request);
    await page.route('**/api/geomaps/**', async route => {
      const { pathname } = new URL(route.request().url());
      let body: unknown;
      if (route.request().method() === 'GET' && pathname.endsWith('/registry')) {
        body = {
          schema: 'ananta.geomap-registry.v1',
          version: 1,
          maps: [{
            id: 'de-states', label: 'Deutsche Länder', level: 'subdivision', format: 'geojson',
            source: 'E2E fixture', featureIdPath: 'properties.id', dataJoinKey: 'region',
            supportedRenderers: ['echarts'], bounds: [13, 52, 14, 53], license: 'CC0',
            attribution: 'Administrative boundaries derived from E2E fixture', minimumMatchRatio: 0.9,
          }],
        };
      } else if (route.request().method() === 'GET' && pathname.endsWith('/de-states/geometry')) {
        body = {
          type: 'FeatureCollection',
          features: [
            { type: 'Feature', properties: { id: 'DE-BE', name: 'Berlin' }, geometry: { type: 'Polygon', coordinates: [[[13, 52], [14, 52], [14, 53], [13, 52]]] } },
            { type: 'Feature', properties: { id: 'DE-BB', name: 'Brandenburg' }, geometry: { type: 'Polygon', coordinates: [[[12, 51], [15, 51], [15, 54], [12, 51]]] } },
          ],
        };
      } else if (route.request().method() === 'POST' && pathname.endsWith('/project')) {
        body = {
          schema: 'ananta.geomap-projection.v1', map_id: 'de-states', registry_version: 1,
          aggregation: 'preaggregated',
          values: [
            { region_id: 'DE-BE', name: 'Berlin', value: 2, source_rows: 1 },
            { region_id: 'DE-BB', name: 'Brandenburg', value: 4, source_rows: 1 },
          ],
          report: {
            matched: ['DE-BE', 'DE-BB'], unmatched: [], duplicates: [], missing_geometry: [],
            invalid_values: [], match_ratio: 1, minimum_match_ratio: 0.9,
            publication_eligible: true, reason_codes: [],
          },
          map_attribution: 'Administrative boundaries derived from E2E fixture',
          data_attribution: 'Fixture data',
        };
      } else {
        await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' });
        return;
      }
      await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) });
    });

    await gotoProjectScopedRoute(page, '/geomaps', { settleNetworkIdle: false });
    await page.getByLabel('Karte', { exact: true }).selectOption('de-states');
    await page.locator('#geomap-csv').setInputFiles({
      name: 'states.csv', mimeType: 'text/csv', buffer: Buffer.from('region,value\nDE-BE,2\nDE-BB,4\n'),
    });
    await page.getByLabel('Datenquellen-Attribution').fill('Fixture data');
    await page.getByRole('button', { name: 'Entwurf speichern' }).click();
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByLabel('Karte', { exact: true })).toHaveValue('de-states');
    await expect(page.getByLabel('Datenquellen-Attribution')).toHaveValue('Fixture data');
    await page.locator('#geomap-csv').setInputFiles({
      name: 'states.csv', mimeType: 'text/csv', buffer: Buffer.from('region,value\nDE-BE,2\nDE-BB,4\n'),
    });
    const preview = page.getByRole('button', { name: 'Vorschau prüfen' });
    await preview.click();

    await expect(preview).toBeEnabled();
    await expect(page.getByText('Automatisch veröffentlichbar')).toBeVisible();
    const berlin = page.getByRole('button', { name: 'Berlin: 2' });
    await berlin.focus();
    await berlin.press('Enter');
    await expect(berlin).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: 'Zoom zurücksetzen' }).click();
    await expect(page.getByText(/Fixture data · Administrative boundaries derived/)).toBeVisible();
    const accessibility = await new AxeBuilder({ page })
      .include('app-geomap-studio-page')
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    expect(accessibility.violations).toEqual([]);
  });
});
