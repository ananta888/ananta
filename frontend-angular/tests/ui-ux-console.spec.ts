import { test, expect } from '@playwright/test';
import {
  assertErrorOverlaysInViewport,
  assertNoUnhandledBrowserErrors,
  clearBrowserErrorGuards,
  login,
} from './utils';

test.describe('UI UX console and visibility', () => {
  test('core navigation has no browser console/page errors', async ({ page }) => {
    clearBrowserErrorGuards(page);
    await login(page);

    const checks: Array<{ path: string; heading: RegExp }> = [
      { path: '/dashboard', heading: /System Dashboard/i },
      { path: '/agents', heading: /^Agenten$/i },
      { path: '/board', heading: /^Board$/i },
      { path: '/templates', heading: /Templates \(Hub\)/i },
      { path: '/teams', heading: /Teams/i },
      { path: '/settings', heading: /System-Einstellungen/i },
    ];

    for (const check of checks) {
      await page.goto(check.path, { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: check.heading }).first()).toBeVisible({ timeout: 20000 });
      await page.waitForTimeout(250);
    }

    await assertNoUnhandledBrowserErrors(page);
  });

  test('error notification is visible in viewport and closeable', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    await page.getByRole('button', { name: 'System' }).click();

    const rawCard = page.locator('.card', { has: page.getByRole('heading', { name: /Roh-Konfiguration/i }) });
    await rawCard.locator('textarea').fill('{');
    const rawSave = rawCard.getByRole('button', { name: /Roh-Daten Speichern/i });
    await rawSave.click();

    const inlineError = rawCard.locator('.error-text');
    await expect(inlineError).toBeVisible({ timeout: 10000 });
    await expect(rawSave).toBeDisabled();
    await page.waitForTimeout(350);

    await assertErrorOverlaysInViewport(page);
  });

  test('templates form stays editable when usage lookups fail', async ({ page }) => {
    await login(page);

    await page.route('**/*', async (route) => {
      const pathname = new URL(route.request().url()).pathname;
      if (pathname === '/teams' || pathname === '/teams/roles' || pathname === '/teams/types') {
        await route.fulfill({ status: 503, body: JSON.stringify({ error: 'e2e forced team metadata fail' }) });
      } else {
        await route.continue();
      }
    });

    await page.goto('/templates', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();
    await expect(page.getByRole('button', { name: /Aktualisieren/i })).toBeEnabled({ timeout: 15000 });

    const nameInput = page.locator('input[placeholder="Name"]').first();
    const nonAdminHint = page.getByText(/Template-Verwaltung ist nur fuer Admins verfuegbar/i);
    if (await nonAdminHint.isVisible().catch(() => false)) {
      await expect(nameInput).toBeDisabled();
      return;
    }

    await expect(nameInput).toBeEnabled({ timeout: 15000 });
    await nameInput.fill(`E2E UX ${Date.now()}`);
    await expect(nameInput).toHaveValue(/E2E UX/);
  });

  test('dashboard, templates, and teams settle without stuck loading states', async ({ page }) => {
    await login(page);

    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    await expect.poll(async () => {
      const quickGoalVisible = await page.getByLabel(/Quick Goal Beschreibung eingeben/i).isVisible().catch(() => false);
      const loadingCount = await page.getByText(/Lade Statistiken von Hub/i).count();
      const dashboardErrorCount = await page.locator('.card.danger', { hasText: /Dashboard-Daten konnten nicht geladen werden/i }).count();
      return quickGoalVisible || loadingCount === 0 || dashboardErrorCount > 0;
    }, { timeout: 35000 }).toBeTruthy();

    await page.goto('/templates', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();
    await expect(page.getByPlaceholder('Name')).toBeVisible({ timeout: 30000 });
    await expect(page.getByRole('button', { name: /Anlegen \/ Speichern/i })).toBeEnabled({ timeout: 30000 });

    await page.goto('/teams', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /Teams werden ueber Blueprints erstellt/i })).toBeVisible();
    await expect(page.locator('.teams-editor-panel')).toBeVisible({ timeout: 30000 });
    await expect(page.locator('.teams-editor-panel').getByLabel('Name')).toBeVisible({ timeout: 30000 });

    await assertNoUnhandledBrowserErrors(page);
  });
});
