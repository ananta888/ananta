import { test, expect } from '@playwright/test';
import { login, ADMIN_USERNAME, ADMIN_PASSWORD, resetAdminPassword } from './utils';

test.describe('Password Change', () => {
  test('should change password and login with new password', async ({ page }) => {
    const newPassword = 'NewAdmin1!Pass';

    try {
      // 1. Login with current password
      await login(page);

      // 2. Go to settings
      await page.goto('/settings');
      await page.waitForLoadState('networkidle');
      await expect(page.getByText('Passwort Ã¤ndern')).toBeVisible();

      // 3. Fill change password form
      await page.locator('input[name="oldPassword"]').fill(ADMIN_PASSWORD);
      await page.locator('input[name="newPassword"]').fill(newPassword);
      await page.locator('input[name="confirmPassword"]').fill(newPassword);
      
      const changePasswordPromise = page.waitForResponse(res => res.url().includes('/change-password'));
      await page.getByRole('button', { name: 'Passwort Ã¤ndern', exact: true }).click();
      await changePasswordPromise;

      await expect(page.locator('.success-msg')).toBeVisible();
      await expect(page.getByText('Passwort erfolgreich geÃ¤ndert!')).toBeVisible();

      // 4. Logout
      await page.getByRole('button', { name: /Logout/i }).click();

      // 5. Try login with old password (should fail)
      await page.locator('input[name="username"]').fill(ADMIN_USERNAME);
      await page.locator('input[name="password"]').fill(ADMIN_PASSWORD);
      
      const failedLoginPromise = page.waitForResponse(res => res.url().includes('/login'));
      await page.getByRole('button', { name: 'Anmelden' }).click();
      await failedLoginPromise;
      
      await expect(page.locator('.error-msg')).toBeVisible();

      // 6. Login with new password (should succeed)
      await page.locator('input[name="username"]').fill(ADMIN_USERNAME);
      await page.locator('input[name="password"]').fill(newPassword);
      
      const successLoginPromise = page.waitForResponse(res => res.url().includes('/login'));
      await page.getByRole('button', { name: 'Anmelden' }).click();
      await successLoginPromise;
      await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    } finally {
      resetAdminPassword();
    }
  });
});
