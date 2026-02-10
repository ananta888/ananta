import { test, expect } from '@playwright/test';
import { login, resetAdminMfaState, ADMIN_USERNAME, ADMIN_PASSWORD } from './utils';
import { generate } from 'otplib';

test.describe('MFA Flow', () => {
  test('should enable and disable MFA', async ({ page }) => {
    resetAdminMfaState();

    try {
      await login(page);

      await page.goto('/settings');
      await expect(page.getByText('Multi-Faktor-Authentifizierung (MFA)')).toBeVisible();

      const setupButton = page.getByRole('button', { name: 'MFA Einrichten' });
      await expect(setupButton).toBeVisible();
      
      const mfaSetupPromise = page.waitForResponse(res => res.url().includes('/mfa/setup') && res.request().method() === 'POST');
      await setupButton.click();
      const mfaSetupResponse = await mfaSetupPromise;
      const mfaSetupPayload = await mfaSetupResponse.json();
      const mfaSetupData = mfaSetupPayload?.data ?? mfaSetupPayload;

      await expect(page.locator('input[placeholder="000000"]')).toBeVisible();
      const secret = String(mfaSetupData?.secret || '').trim() || (await page.locator('code').innerText()).trim();
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

      await page.fill('input[name="username"]', ADMIN_USERNAME);
      await page.fill('input[name="password"]', ADMIN_PASSWORD);
      await page.click('button.primary');

      await expect(page.getByText('MFA Code / Backup Code')).toBeVisible();
      await page.fill('input[name="mfaToken"]', backupCode);
      await expect(page.locator('input[name="mfaToken"]')).toHaveValue(backupCode);
      await page.click('button.primary');

      await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();

      await page.goto('/settings');
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
