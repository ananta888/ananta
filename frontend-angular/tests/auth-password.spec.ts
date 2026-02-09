import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Password Change', () => {
  test('should change password and login with new password', async ({ page }) => {
    // 1. Login with current password
    await login(page);

    // 2. Go to settings
    await page.goto('/settings');
    await page.waitForLoadState('networkidle');
    await expect(page.getByText('Passwort ändern')).toBeVisible();

    // 3. Fill change password form
    await page.locator('input[name="oldPassword"]').fill('admin');
    await page.locator('input[name="newPassword"]').fill('new-admin-password');
    await page.locator('input[name="confirmPassword"]').fill('new-admin-password');
    
    const changePasswordPromise = page.waitForResponse(res => res.url().includes('/change-password'));
    await page.getByRole('button', { name: 'Passwort ändern', exact: true }).click();
    await changePasswordPromise;

    await expect(page.locator('.success-msg')).toBeVisible();
    await expect(page.getByText('Passwort erfolgreich geändert!')).toBeVisible();

    // 4. Logout
    await page.getByRole('button', { name: /Logout/i }).click();

    // 5. Try login with old password (should fail)
    await page.locator('input[name="username"]').fill('admin');
    await page.locator('input[name="password"]').fill('admin');
    
    const failedLoginPromise = page.waitForResponse(res => res.url().includes('/login'));
    await page.getByRole('button', { name: 'Anmelden' }).click();
    await failedLoginPromise;
    
    await expect(page.locator('.error-msg')).toBeVisible();

    // 6. Login with new password (should succeed)
    await page.locator('input[name="username"]').fill('admin');
    await page.locator('input[name="password"]').fill('new-admin-password');
    
    const successLoginPromise = page.waitForResponse(res => res.url().includes('/login'));
    await page.getByRole('button', { name: 'Anmelden' }).click();
    await successLoginPromise;
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();

    // Reset password back to 'admin' for other tests
    await page.goto('/settings');
    await page.waitForLoadState('networkidle');
    await page.locator('input[name="oldPassword"]').fill('new-admin-password');
    await page.locator('input[name="newPassword"]').fill('admin');
    await page.locator('input[name="confirmPassword"]').fill('admin');
    
    const resetPasswordPromise = page.waitForResponse(res => res.url().includes('/change-password'));
    await page.getByRole('button', { name: 'Passwort ändern', exact: true }).click();
    await resetPasswordPromise;
    
    await expect(page.locator('.success-msg')).toBeVisible();
  });
});
