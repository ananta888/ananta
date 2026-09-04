import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

import { gotoProjectScopedRoute, loginFast } from './utils';

test.describe('GeoMap Studio', () => {
  test('CSV to offline map is keyboard-operable and policy-gated without human input', async ({ page, request }) => {
    test.setTimeout(120_000);
    await loginFast(page, request);

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
