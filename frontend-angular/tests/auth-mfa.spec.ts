import { test, expect } from '@playwright/test';
import { login } from './utils';
import { authenticator } from 'otplib';

test.describe('MFA Flow', () => {
  test('should enable and disable MFA', async ({ page }) => {
    // 1. Login
    await login(page);

    // 2. Gehe zu Einstellungen
    await page.goto('/settings');
    await expect(page.getByText('Multi-Faktor-Authentifizierung (MFA)')).toBeVisible();

    // 3. Start MFA Setup
    const setupButton = page.getByRole('button', { name: 'MFA Einrichten' });
    await expect(setupButton).toBeVisible();
    await setupButton.click();

    // 4. Verify QR Code and Secret are shown
    await expect(page.locator('.qr-code img')).toBeVisible();
    await expect(page.locator('code')).toBeVisible();
    const secret = await page.locator('code').innerText();
    expect(secret.length).toBeGreaterThan(10);

    // 5. Token generieren und verifizieren
    const token = authenticator.generate(secret);
    await page.fill('input[placeholder="000000"]', token);
    await page.click('button:has-text("Aktivieren")');

    // 6. Backup-Codes prüfen
    await expect(page.getByText('MFA Backup-Codes')).toBeVisible();
    await page.click('button:has-text("Ich habe die Codes gespeichert")');

    // 7. Status prüfen
    await expect(page.getByText('MFA ist für Ihr Konto aktiviert.')).toBeVisible();

    // 8. Logout und Login mit MFA testen
    await page.goto('/login');
    await page.evaluate(() => localStorage.removeItem('ananta.auth.v1')); // Nur Auth-Token löschen
    await page.reload();

    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin');
    await page.click('button.primary');

    // MFA Token Abfrage
    await expect(page.getByText('MFA Code / Backup Code')).toBeVisible();
    const loginToken = authenticator.generate(secret);
    await page.fill('input[name="mfaToken"]', loginToken);
    await page.click('button.primary');

    // Dashboard erreicht?
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();

    // 9. MFA wieder deaktivieren für sauberen Test-State
    await page.goto('/settings');
    const disableButton = page.getByRole('button', { name: 'MFA Deaktivieren' });
    
    // Playwright handles the confirm() dialog automatically if we don't handle it, 
    // but better to be explicit or just let it pass if default is OK.
    page.once('dialog', dialog => dialog.accept());
    await disableButton.click();

    await expect(setupButton).toBeVisible();
  });
});
