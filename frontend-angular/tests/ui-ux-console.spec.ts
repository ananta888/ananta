import { test, expect, type Page } from '@playwright/test';
import { login } from './utils';

function attachConsoleCollectors(page: Page) {
  const consoleErrors: string[] = [];
  const pageErrors: string[] = [];

  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const text = msg.text().trim();
      if (!text) return;
      if (text.includes('favicon.ico')) return;
      if (text.includes('Failed to load resource: the server responded with a status of 401')) return;
      consoleErrors.push(text);
    }
  });

  page.on('pageerror', (err) => {
    pageErrors.push((err?.message || String(err)).trim());
  });

  return { consoleErrors, pageErrors };
}

test.describe('UI UX console and visibility', () => {
  test('core navigation has no browser console/page errors', async ({ page }) => {
    const logs = attachConsoleCollectors(page);
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

    expect(logs.consoleErrors, `Console errors:\n${logs.consoleErrors.join('\n')}`).toEqual([]);
    expect(logs.pageErrors, `Page errors:\n${logs.pageErrors.join('\n')}`).toEqual([]);
  });

  test('error notification is visible in viewport and closeable', async ({ page }) => {
    await login(page);
    await page.goto('/settings');
    await page.getByRole('button', { name: 'System' }).click();

    const rawCard = page.locator('.card', { has: page.getByRole('heading', { name: /Roh-Konfiguration/i }) });
    await rawCard.locator('textarea').fill('{');
    await rawCard.getByRole('button', { name: /Roh-Daten Speichern/i }).click();

    const toast = page.locator('.notification.error').first();
    await expect(toast).toBeVisible({ timeout: 10000 });
    await page.waitForTimeout(350);

    const viewport = page.viewportSize();
    expect(viewport).toBeTruthy();
    const box = await toast.boundingBox();
    expect(box).toBeTruthy();
    if (viewport && box) {
      expect(box.x).toBeGreaterThanOrEqual(0);
      expect(box.y).toBeGreaterThanOrEqual(0);
      expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
      expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
    }

    await toast.locator('.notification-close').click();
    await expect(page.locator('.notification.error')).toHaveCount(0);
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
});
