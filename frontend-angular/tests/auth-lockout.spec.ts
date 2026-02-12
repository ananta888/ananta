import { test, expect } from '@playwright/test';
import { HUB_URL, createUserAsAdmin, deleteUserAsAdmin, prepareLoginPage } from './utils';

test.describe('Auth Lockout', () => {
  test('locked account shows error message', async ({ page, request }) => {
    const username = `lockout_${Date.now()}`;
    const validPassword = 'LockoutUser1!A';
    const wrongPassword = 'WrongPassword1!A';
    await createUserAsAdmin(username, validPassword);

    try {
      for (let i = 0; i < 5; i += 1) {
        const failed = await request.post(`${HUB_URL}/login`, {
          data: { username, password: wrongPassword }
        });
        expect(failed.status()).toBe(401);
      }

      await prepareLoginPage(page);
      const res = await request.post(`${HUB_URL}/login`, {
        data: { username, password: validPassword }
      });
      expect(res.status()).toBe(403);
      await expect(page).toHaveURL(/\/login/);
    } finally {
      await deleteUserAsAdmin(username);
    }
  });
});
