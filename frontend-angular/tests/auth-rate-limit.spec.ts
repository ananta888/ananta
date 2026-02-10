import { test, expect } from '@playwright/test';
import { ADMIN_USERNAME, ADMIN_PASSWORD, HUB_URL, seedLoginAttempts, clearLoginAttempts, prepareLoginPage } from './utils';

test.describe('Auth Rate Limit', () => {
  test('too many attempts returns rate limit error', async ({ page, request }) => {
    const ip = '127.0.0.1';
    seedLoginAttempts(ip, 10);

    try {
      await prepareLoginPage(page);
      const res = await request.post(`${HUB_URL}/login`, {
        data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
      });
      expect(res.status()).toBe(429);
      await expect(page).toHaveURL(/\/login/);
    } finally {
      clearLoginAttempts(ip);
    }
  });
});
