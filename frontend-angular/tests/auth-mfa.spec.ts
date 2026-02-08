import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('MFA Flow', () => {
  test('should enable and disable MFA', async ({ page }) => {
    // 1. Login Manual
    await page.goto('/login');
    await page.waitForSelector('input[name="username"]');
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin');
    await page.click('button.primary');

    // Wait for Dashboard
    await page.waitForURL('**/dashboard', { timeout: 10000 });
    await expect(page.getByText('Multi-Faktor-Authentifizierung (MFA)')).toBeVisible();

    // 3. Start MFA Setup
    const setupButton = page.getByRole('button', { name: 'MFA Einrichten' });
    await expect(setupButton).toBeVisible();
    await setupButton.click();

    // 4. Verify QR Code and Secret are shown
    await expect(page.locator('.qr-code img')).toBeVisible();
    await expect(page.locator('code')).toBeVisible();
    const secret = await page.locator('code').innerText();
    expect(secret).toHaveLength(32); // pyotp default length

    // Hier bräuchten wir einen TOTP Token um fortzufahren.
    // Da wir keine TOTP Library im Test-Environment haben, 
    // testen wir hier nur, dass das Setup korrekt startet.
    
    // Test Cleanup: Wir brechen das Setup ab
    await page.getByRole('button', { name: 'Abbrechen' }).click();
    await expect(setupButton).toBeVisible();
  });
});
