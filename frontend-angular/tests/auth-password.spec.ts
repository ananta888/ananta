import { test, expect } from '@playwright/test';
import { login, ADMIN_USERNAME, ADMIN_PASSWORD, createUserAsAdmin, deleteUserAsAdmin } from './utils';

test.describe('Password Change', () => {
  test('should change password and login with new password', async ({ page }) => {
    const username = `pwd_${Date.now()}`;
    const initialPassword = 'InitialUser1!A';
    const newPassword = 'NewUserPass1!A';
    await createUserAsAdmin(username, initialPassword);

    try {
      await login(page, username, initialPassword);

      await page.goto('/settings');
      await expect(page.locator('input[name="oldPassword"]')).toBeVisible();

      await page.locator('input[name="oldPassword"]').fill(initialPassword);
      await page.locator('input[name="newPassword"]').fill(newPassword);
      await page.locator('input[name="confirmPassword"]').fill(newPassword);

      const changePasswordPromise = page.waitForResponse(
        (res) => res.url().includes('/change-password') && res.request().method() === 'POST'
      );
      await page.locator('form button.primary').click();
      const changePasswordResponse = await changePasswordPromise;
      expect(changePasswordResponse.ok()).toBeTruthy();

      await page.getByRole('button', { name: /logout/i }).click();
      await expect(page.locator('input[name="username"]')).toBeVisible();

      await page.locator('input[name="username"]').fill(username);
      await page.locator('input[name="password"]').fill(initialPassword);
      const failedLoginPromise = page.waitForResponse(
        (res) => res.url().includes('/login') && res.request().method() === 'POST'
      );
      await page.locator('button.primary').click();
      const failedLoginResponse = await failedLoginPromise;
      expect(failedLoginResponse.status()).toBe(401);

      await page.locator('input[name="username"]').fill(username);
      await page.locator('input[name="password"]').fill(newPassword);
      const successLoginPromise = page.waitForResponse(
        (res) => res.url().includes('/login') && res.request().method() === 'POST'
      );
      await page.locator('button.primary').click();
      const successLoginResponse = await successLoginPromise;
      expect(successLoginResponse.status()).toBe(200);
      await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    } finally {
      // Ensure admin login still works before cleanup.
      await login(page, ADMIN_USERNAME, ADMIN_PASSWORD);
      await deleteUserAsAdmin(username);
    }
  });
});
