import { test, expect } from '@playwright/test';
import { ADMIN_USERNAME, ADMIN_PASSWORD, HUB_URL, setUserLockout, clearUserLockout, prepareLoginPage } from './utils';

test.describe('Auth Lockout', () => {
  test('locked account shows error message', async ({ page, request }) => {
    setUserLockout();

    try {
      await prepareLoginPage(page);
      const res = await request.post(`${HUB_URL}/login`, {
        data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
      });
      expect(res.status()).toBe(403);
      await expect(page).toHaveURL(/\/login/);
    } finally {
      clearUserLockout();
    }
  });
});
