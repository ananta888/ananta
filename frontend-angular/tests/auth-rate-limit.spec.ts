import { test, expect } from '@playwright/test';
import { ADMIN_USERNAME, ADMIN_PASSWORD, HUB_URL, TEST_LOGIN_IP, clearLoginAttempts, getAccessToken } from './utils';

test.describe('Auth Rate Limit', () => {
  test.beforeEach(() => {
    if (TEST_LOGIN_IP) {
      clearLoginAttempts(TEST_LOGIN_IP);
    }
  });

  test('too many attempts returns rate limit error', async ({ request }) => {
    test.setTimeout(120_000);
    const username = `no_such_user_${Date.now()}`;
    const wrongPassword = 'WrongPassword1!A';
    const adminToken = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);
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
    const resetRes = await request.post(`${HUB_URL}/test/reset-login-attempts`, {
      headers: { Authorization: `Bearer ${adminToken}` },
      data: TEST_LOGIN_IP ? { ip: TEST_LOGIN_IP, clear_ban: true } : { clear_ban: true }
    });
    expect(resetRes.ok()).toBeTruthy();
    const cleanupLogin = await request.post(`${HUB_URL}/login`, {
      data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
    });
    expect(cleanupLogin.status()).toBe(200);
  });
});
