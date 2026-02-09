import { test, expect } from '@playwright/test';
import { execSync } from 'node:child_process';
import { login } from './utils';
import { generate } from 'otplib';

function resetAdminMfaState() {
  execSync(
    "docker exec ananta-postgres-1 psql -U ananta -d ananta -c \"UPDATE users SET mfa_enabled = false, mfa_secret = NULL, mfa_backup_codes = '[]'::json, failed_login_attempts = 0, lockout_until = NULL WHERE username = 'admin';\"",
    { stdio: 'ignore' }
  );
}

test.describe('MFA Flow', () => {
  test('should enable and disable MFA', async ({ page }) => {
    resetAdminMfaState();

    try {
      await login(page);

      await page.goto('/settings');
      await page.waitForLoadState('networkidle');
      await expect(page.getByText('Multi-Faktor-Authentifizierung (MFA)')).toBeVisible();

      const setupButton = page.getByRole('button', { name: 'MFA Einrichten' });
      await expect(setupButton).toBeVisible();
      
      const mfaSetupPromise = page.waitForResponse(res => res.url().includes('/mfa/setup'));
      await setupButton.click();
      await mfaSetupPromise;

      await expect(page.locator('.qr-code img')).toBeVisible();
      await expect(page.locator('code')).toBeVisible();
      const secret = (await page.locator('code').innerText()).trim();
      expect(secret.length).toBeGreaterThan(10);

      const token = String(await generate({ secret }));
      await page.fill('input[placeholder="000000"]', token);
      
      const mfaEnablePromise = page.waitForResponse(res => res.url().includes('/mfa/enable'));
      await page.click('button:has-text("Aktivieren")');
      await mfaEnablePromise;

      await expect(page.getByText('MFA Backup-Codes')).toBeVisible();
      const backupCode = (await page.locator('.card.success .grid.cols-2 > div').first().innerText()).trim();
      expect(backupCode.length).toBeGreaterThan(0);
      await page.click('button:has-text("Ich habe die Codes gespeichert")');

      await expect(page.getByText('MFA ist für Ihr Konto aktiviert.')).toBeVisible();

      await page.goto('/login');
      await page.evaluate(() => {
        localStorage.removeItem('ananta.user.token');
        localStorage.removeItem('ananta.user.refresh_token');
      });
      await page.reload();

      await page.fill('input[name="username"]', 'admin');
      await page.fill('input[name="password"]', 'admin');
      await page.click('button.primary');

      await expect(page.getByText('MFA Code / Backup Code')).toBeVisible();
      await page.fill('input[name="mfaToken"]', backupCode);
      await expect(page.locator('input[name="mfaToken"]')).toHaveValue(backupCode);
      await page.click('button.primary');

      await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();

      await page.goto('/settings');
      await page.waitForLoadState('networkidle');
      const disableButton = page.getByRole('button', { name: 'MFA Deaktivieren' });
      page.once('dialog', dialog => dialog.accept());
      
      const mfaDisablePromise = page.waitForResponse(res => res.url().includes('/mfa/disable'));
      await disableButton.click();
      await mfaDisablePromise;

      await expect(setupButton).toBeVisible();
    } finally {
      resetAdminMfaState();
    }
  });
});
