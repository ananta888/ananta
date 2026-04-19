import { test, expect } from '@playwright/test';
import { assertNoUnhandledBrowserErrors, clearBrowserErrorGuards, loginFast } from './utils';

test.describe('Admin Core Journey', () => {
  test('navigates core areas', async ({ page, request }) => {
    test.setTimeout(120_000);
    clearBrowserErrorGuards(page);
    await loginFast(page, request);
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText(/System Dashboard|Ananta starten/i)).toBeVisible();

    await page.goto('/teams', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /Teams werden ueber Blueprints erstellt/i })).toBeVisible();

    await page.goto('/templates', { waitUntil: 'domcontentloaded' });
    await expect(page.getByRole('heading', { name: /Templates \(Hub\)/i })).toBeVisible();

    await page.goto('/settings', { waitUntil: 'domcontentloaded' });
    await expect(page.getByText(/System-Einstellungen/i)).toBeVisible();
    await assertNoUnhandledBrowserErrors(page);
  });
});
