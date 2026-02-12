import { test, expect } from '@playwright/test';
import { login, ADMIN_USERNAME, ADMIN_PASSWORD } from './utils';

test.describe('Password Change', () => {
  test.skip(process.env.ANANTA_E2E_USE_EXISTING === '1', 'Requires sqlite-backed E2E fixture reset.');

  test('should change password and login with new password', async ({ page }) => {
    const newPassword = 'NewAdmin1!Pass';
    await login(page);

    await page.goto('/settings');
    await expect(page.locator('input[name="oldPassword"]')).toBeVisible();

    await page.locator('input[name="oldPassword"]').fill(ADMIN_PASSWORD);
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

    await page.locator('input[name="username"]').fill(ADMIN_USERNAME);
    await page.locator('input[name="password"]').fill(ADMIN_PASSWORD);
    const failedLoginPromise = page.waitForResponse(
      (res) => res.url().includes('/login') && res.request().method() === 'POST'
    );
    await page.locator('button.primary').click();
    const failedLoginResponse = await failedLoginPromise;
    expect(failedLoginResponse.status()).toBe(401);

    await page.locator('input[name="username"]').fill(ADMIN_USERNAME);
    await page.locator('input[name="password"]').fill(newPassword);
    const successLoginPromise = page.waitForResponse(
      (res) => res.url().includes('/login') && res.request().method() === 'POST'
    );
    await page.locator('button.primary').click();
    const successLoginResponse = await successLoginPromise;
    expect(successLoginResponse.status()).toBe(200);
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();

    await page.goto('/settings');
    await page.locator('input[name="oldPassword"]').fill(newPassword);
    await page.locator('input[name="newPassword"]').fill(ADMIN_PASSWORD);
    await page.locator('input[name="confirmPassword"]').fill(ADMIN_PASSWORD);
    const restorePasswordPromise = page.waitForResponse(
      (res) => res.url().includes('/change-password') && res.request().method() === 'POST'
    );
    await page.locator('form button.primary').click();
    const restorePasswordResponse = await restorePasswordPromise;
    expect(restorePasswordResponse.ok()).toBeTruthy();
  });
});
