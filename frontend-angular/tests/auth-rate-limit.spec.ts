import { test, expect } from '@playwright/test';
import { ADMIN_USERNAME, ADMIN_PASSWORD, HUB_URL } from './utils';

test.describe('Auth Rate Limit', () => {
  test('too many attempts returns rate limit error', async ({ request }) => {
    const username = `no_such_user_${Date.now()}`;
    const wrongPassword = 'WrongPassword1!A';

    for (let i = 0; i < 10; i += 1) {
      const res = await request.post(`${HUB_URL}/login`, {
        data: { username, password: wrongPassword }
      });
      expect(res.status()).toBe(401);
    }

    const res = await request.post(`${HUB_URL}/login`, {
      data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
    });
    expect(res.status()).toBe(429);

    // Wait for short-window rate limit (60s) to expire to avoid cross-test bleed.
    await new Promise((resolve) => setTimeout(resolve, 65000));
    const cleanupLogin = await request.post(`${HUB_URL}/login`, {
      data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
    });
    if (cleanupLogin.status() === 200) {
      expect(cleanupLogin.ok()).toBeTruthy();
    } else {
      // Existing deployments may enforce stronger policies; keep core assertion above deterministic.
      expect([401, 403, 429]).toContain(cleanupLogin.status());
    }
  });
});
