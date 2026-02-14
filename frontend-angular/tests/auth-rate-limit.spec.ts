import { test, expect } from '@playwright/test';
import { ADMIN_USERNAME, ADMIN_PASSWORD, HUB_URL, clearLoginAttempts } from './utils';

test.describe('Auth Rate Limit', () => {
  test('too many attempts returns rate limit error', async ({ request }) => {
    const username = `no_such_user_${Date.now()}`;
    const wrongPassword = 'WrongPassword1!A';
    let hitRateLimit = false;

    for (let i = 0; i < 30; i += 1) {
      const res = await request.post(`${HUB_URL}/login`, {
        data: { username, password: wrongPassword }
      });
      if (res.status() === 429) {
        hitRateLimit = true;
        break;
      }
      expect([401, 403]).toContain(res.status());
    }
    expect(hitRateLimit).toBeTruthy();

    // Prevent cross-test bleed from IP-based throttling.
    clearLoginAttempts('127.0.0.1');
    const cleanupLogin = await request.post(`${HUB_URL}/login`, {
      data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
    });
    expect(cleanupLogin.status()).toBe(200);
  });
});
