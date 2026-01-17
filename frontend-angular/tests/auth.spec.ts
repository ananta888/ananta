import { test, expect } from '@playwright/test';

test.describe('Auth Flow', () => {
  test('login succeeds with default admin', async ({ page }) => {
    await page.goto('/login');
    await page.evaluate(() => localStorage.clear());

    await page.getByLabel('Benutzername').fill('admin');
    await page.getByLabel('Passwort').fill('admin');
    await page.getByRole('button', { name: 'Anmelden' }).click();

    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
  });

  test('login shows error on invalid credentials', async ({ page }) => {
    await page.goto('/login');
    await page.evaluate(() => localStorage.clear());

    await page.getByLabel('Benutzername').fill('admin');
    await page.getByLabel('Passwort').fill('wrong');
    await page.getByRole('button', { name: 'Anmelden' }).click();

    await expect(page.getByText(/Login fehlgeschlagen|Invalid/i)).toBeVisible();
  });
});
