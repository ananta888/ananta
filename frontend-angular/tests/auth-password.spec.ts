import { test, expect } from '@playwright/test';
import { loginFast, HUB_URL, createUserAsAdmin, deleteUserAsAdmin } from './utils';

test.describe('Password Change', () => {
  test('should change password and login with new password', async ({ page, request }) => {
    test.setTimeout(120_000);
    const username = `pwd_${Date.now()}`;
    const initialPassword = 'InitialUser1!A';
    const newPassword = 'NewUserPass1!A';
    await createUserAsAdmin(username, initialPassword);

    try {
      await loginFast(page, request, username, initialPassword);
      const accessToken = await page.evaluate(() => localStorage.getItem('ananta.user.token'));
      expect(accessToken).toBeTruthy();

      const changePasswordResponse = await request.post(`${HUB_URL}/change-password`, {
        headers: { Authorization: `Bearer ${accessToken}` },
        data: {
          old_password: initialPassword,
          new_password: newPassword,
        },
      });
      expect(changePasswordResponse.ok()).toBeTruthy();

      const failedLoginResponse = await request.post(`${HUB_URL}/login`, {
        data: { username, password: initialPassword }
      });
      expect(failedLoginResponse.status()).toBe(401);

      const successLoginResponse = await request.post(`${HUB_URL}/login`, {
        data: { username, password: newPassword }
      });
      expect(successLoginResponse.ok()).toBeTruthy();
      const successPayload = await successLoginResponse.json();
      const newAccessToken = String(successPayload?.data?.access_token || '').trim();
      expect(newAccessToken.length).toBeGreaterThan(10);

      await loginFast(page, request, username, newPassword);
      await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
      await expect(page.getByRole('heading', { name: /System Dashboard|Ananta starten/i })).toBeVisible();
    } finally {
      await deleteUserAsAdmin(username);
    }
  });
});
