import AxeBuilder from '@axe-core/playwright';
import { expect, test, type Page } from '@playwright/test';

import {
  GRAPH_XSS_LABEL,
  GRAPH_XSS_RELATION,
  createFunctionalGraphArtifact,
  expectNoGraphHttpSince,
  installGraphApiMocks,
  installLocalGraphIdentity,
  openGraphInternals,
  trackHttpRequests,
  waitForTwoDimensionalRenderer,
} from './helpers/codecompass-graph-visualization';

const WCAG_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'];

async function expectNoBlockingAxeViolations(page: Page, selector: string): Promise<void> {
  const result = await new AxeBuilder({ page })
    .include(selector)
    .withTags(WCAG_TAGS)
    .analyze();
  const blocking = result.violations.filter(violation =>
    violation.impact === 'critical' || violation.impact === 'serious');
  expect(blocking, JSON.stringify(blocking, null, 2)).toEqual([]);
}

async function bootstrapGraph(page: Page): Promise<void> {
  await installLocalGraphIdentity(page);
  await installGraphApiMocks(page, createFunctionalGraphArtifact());
  await openGraphInternals(page);
}

test.describe.configure({ mode: 'serial', retries: 0 });

test.describe('CodeCompass graph visualization functional gate', () => {
  test('uses the real Simple, 2D and 3D renderers while legends, filters and profiles remain client-local', async ({ page }) => {
    test.setTimeout(120_000);
    const requests = trackHttpRequests(page);
    await bootstrapGraph(page);

    const viewer = page.getByTestId('codecompass-graph-viewer');
    await expect(viewer.locator('app-simple-graph-view h4').first()).toHaveText('Nodes (4)');
    await expect(viewer.locator('app-simple-graph-view h4').nth(1)).toHaveText('Edges (5)');
    await expect(viewer.getByText(GRAPH_XSS_LABEL, { exact: true })).toBeVisible();
    await expect(viewer.locator('img, svg[onload]')).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => window.__ccgvXssExecuted)).toBe(false);

    const localInteractionMark = requests.mark();
    await viewer.getByTestId('graph-edge-legend-trigger').click();
    const edgeDrawer = viewer.getByTestId('graph-edge-legend-drawer');
    await expect(edgeDrawer).toBeVisible();
    await expect(edgeDrawer.locator('.raw-type').filter({ hasText: GRAPH_XSS_RELATION })).toBeVisible();
    await expect(edgeDrawer.getByText(/Semantik unbekannt/)).toBeVisible();
    await expect(edgeDrawer.locator('img, svg')).toHaveCount(0);
    await expectNoBlockingAxeViolations(page, '[data-testid="graph-edge-legend-drawer"]');
    await page.keyboard.press('Escape');
    await expect(viewer.getByTestId('graph-edge-legend-trigger')).toBeFocused();

    await viewer.getByTestId('graph-domain-legend-trigger').click();
    const domainDrawer = viewer.getByTestId('graph-domain-legend-drawer');
    const alphaToggle = domainDrawer.locator('.domain-entry')
      .filter({ hasText: 'domain-alpha' })
      .locator('input[type="checkbox"]');
    await expect(alphaToggle).toBeChecked();
    await alphaToggle.uncheck();
    await expect(viewer.locator('app-simple-graph-view h4').first()).toHaveText('Nodes (2)');
    await alphaToggle.check();
    await expect(viewer.locator('app-simple-graph-view h4').first()).toHaveText('Nodes (4)');
    await page.keyboard.press('Escape');

    await viewer.getByTestId('graph-visual-settings-trigger').click();
    const settings = viewer.getByTestId('graph-visual-settings-drawer');
    const preset = settings.getByTestId('graph-visual-profile-preset');
    await preset.selectOption('importance');
    await expect(preset).toHaveValue('importance');
    await settings.getByRole('button', { name: 'Exportieren' }).click();
    await expect(settings.getByLabel('Exportiertes Visualisierungsprofil'))
      .toHaveValue(/"profileId":"importance"/);
    await expectNoGraphHttpSince(requests, localInteractionMark);
    await page.keyboard.press('Escape');

    await viewer.getByTestId('graph-view-mode-2d').click();
    await waitForTwoDimensionalRenderer(page);
    await page.evaluate(() => {
      const host = document.querySelector('app-graph-2d-view');
      const component = (window as any).ng.getComponent(host);
      const node = component.cy.nodes().filter((candidate: any) =>
        candidate.data('originalId') === 'alpha-entry').first();
      node.emit({ type: 'mouseover', renderedPosition: { x: 40, y: 40 } });
    });
    const tooltip = viewer.locator('app-graph-2d-view [role="tooltip"]');
    await expect(tooltip).toBeVisible();
    await expect(tooltip).toContainText(GRAPH_XSS_LABEL);
    await expect(tooltip.locator('*')).toHaveCount(0);
    await expect.poll(() => page.evaluate(() => window.__ccgvXssExecuted)).toBe(false);
    await expectNoBlockingAxeViolations(page, 'app-graph-2d-view [role="tooltip"]');

    await viewer.getByTestId('graph-view-mode-3d').click();
    await expect(viewer.locator('app-graph-3d-view')).toBeVisible();
    await expect(viewer.locator('app-graph-3d-view canvas').first()).toBeVisible({ timeout: 60_000 });
    await viewer.getByTestId('graph-view-mode-simple').click();
    await expect(viewer.locator('app-simple-graph-view')).toBeVisible();
    await expect(viewer.locator('app-graph-3d-view')).toHaveCount(0);
    await expectNoGraphHttpSince(requests, localInteractionMark);
  });

  test('invalid, polluted, executable-looking and corrupted stored profiles fail closed across reload', async ({ page }) => {
    test.setTimeout(90_000);
    const requests = trackHttpRequests(page);
    await bootstrapGraph(page);

    const viewer = page.getByTestId('codecompass-graph-viewer');
    await viewer.getByTestId('graph-visual-settings-trigger').click();
    let settings = viewer.getByTestId('graph-visual-settings-drawer');
    let preset = settings.getByTestId('graph-visual-profile-preset');
    await preset.selectOption('importance');
    await settings.getByRole('button', { name: 'Speichern' }).click();
    await settings.getByRole('button', { name: 'Exportieren' }).click();
    const exported = await settings.getByLabel('Exportiertes Visualisierungsprofil').inputValue();
    expect(exported).toContain('"profileId":"importance"');

    const mark = requests.mark();
    const fileInput = settings.locator('input[type="file"]');
    await fileInput.setInputFiles({
      name: 'invalid-profile.json',
      mimeType: 'application/json',
      buffer: Buffer.from('{"not": valid json', 'utf8'),
    });
    await expect(settings.getByRole('alert')).toContainText('invalid_json');
    await expect(preset).toHaveValue('importance');

    const malicious = exported.replace(
      '"domainColorOverrides":{}',
      '"domainColorOverrides":{"__proto__":"#112233","safe":"url(javascript:alert(1))"}',
    );
    expect(malicious).not.toBe(exported);
    await fileInput.setInputFiles({
      name: 'polluted-profile.json',
      mimeType: 'application/json',
      buffer: Buffer.from(malicious, 'utf8'),
    });
    await expect(settings.getByRole('alert')).toContainText('dangerous_property');
    await expect(settings.getByRole('alert')).toContainText('invalid_color');
    await expect(preset).toHaveValue('importance');
    await expect.poll(() => page.evaluate(() => ({
      xss: window.__ccgvXssExecuted,
      polluted: (Object.prototype as Record<string, unknown>)['polluted'],
    }))).toEqual({ xss: false, polluted: undefined });
    await expectNoGraphHttpSince(requests, mark);

    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('codecompass-graph-viewer')).toBeVisible();
    await page.getByTestId('graph-visual-settings-trigger').click();
    settings = page.getByTestId('graph-visual-settings-drawer');
    preset = settings.getByTestId('graph-visual-profile-preset');
    await expect(preset).toHaveValue('importance');

    await page.evaluate(() => {
      const key = Object.keys(localStorage).find(candidate =>
        candidate.startsWith('ananta.codecompass.graph-visual-profile.v1.'));
      if (!key) throw new Error('ccgv_profile_storage_key_missing');
      localStorage.setItem(key, '{"__proto__":{"polluted":true}}');
    });
    await page.reload({ waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('codecompass-graph-viewer')).toBeVisible();
    await page.getByTestId('graph-visual-settings-trigger').click();
    settings = page.getByTestId('graph-visual-settings-drawer');
    await expect(settings.getByTestId('graph-visual-profile-preset')).toHaveValue('structure');
    await expect(settings.getByRole('alert')).toContainText(/dangerous_property|unsupported_schema_version/);
    await expect.poll(() => page.evaluate(() =>
      (Object.prototype as Record<string, unknown>)['polluted'])).toBeUndefined();
  });

  test('mobile drawers trap focus, restore the trigger and have no critical or serious Axe findings', async ({ page }) => {
    test.setTimeout(90_000);
    await page.setViewportSize({ width: 390, height: 844 });
    await bootstrapGraph(page);

    const viewer = page.getByTestId('codecompass-graph-viewer');
    const settingsTrigger = viewer.getByTestId('graph-visual-settings-trigger');
    await settingsTrigger.click();
    const settings = viewer.getByTestId('graph-visual-settings-drawer');
    await expect(settings).toBeVisible();
    const box = await settings.boundingBox();
    expect(box).not.toBeNull();
    expect(box!.x).toBeGreaterThanOrEqual(0);
    expect(box!.x + box!.width).toBeLessThanOrEqual(390);
    expect(box!.y).toBeGreaterThanOrEqual(0);
    expect(box!.y + box!.height).toBeLessThanOrEqual(844);
    await expect.poll(() => page.evaluate(() => {
      const drawer = document.querySelector('[data-testid="graph-visual-settings-drawer"]');
      return Boolean(drawer?.contains(document.activeElement));
    })).toBe(true);
    for (let index = 0; index < 12; index += 1) await page.keyboard.press('Tab');
    await expect.poll(() => page.evaluate(() => {
      const drawer = document.querySelector('[data-testid="graph-visual-settings-drawer"]');
      return Boolean(drawer?.contains(document.activeElement));
    })).toBe(true);
    await expectNoBlockingAxeViolations(page, '[data-testid="graph-visual-settings-drawer"]');
    await page.keyboard.press('Escape');
    await expect(settingsTrigger).toBeFocused();

    const domainTrigger = viewer.getByTestId('graph-domain-legend-trigger');
    await domainTrigger.click();
    const domainDrawer = viewer.getByTestId('graph-domain-legend-drawer');
    await expect(domainDrawer).toBeVisible();
    await expectNoBlockingAxeViolations(page, '[data-testid="graph-domain-legend-drawer"]');
    await page.keyboard.press('Escape');
    await expect(domainTrigger).toBeFocused();

    const edgeTrigger = viewer.getByTestId('graph-edge-legend-trigger');
    await edgeTrigger.click();
    await expectNoBlockingAxeViolations(page, '[data-testid="graph-edge-legend-drawer"]');
    await page.keyboard.press('Escape');
    await expect(edgeTrigger).toBeFocused();
  });
});
