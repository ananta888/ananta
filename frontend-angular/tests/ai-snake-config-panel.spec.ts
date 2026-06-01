import { test, expect } from '@playwright/test';
import { ADMIN_PASSWORD, ADMIN_USERNAME, HUB_URL, getAccessToken } from './utils';

test.describe('AI Snake Config Panel Persistence', () => {
  test('persists backend config value across reads', async ({ request }) => {
    const token = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);
    const key = 'chat_backend';
    const value = `lmstudio`;

    const patch = await request.patch(`${HUB_URL}/ai-snake/config`, {
      headers: { Authorization: `Bearer ${token}` },
      data: { [key]: value },
    });
    expect(patch.ok()).toBeTruthy();

    const read = await request.get(`${HUB_URL}/ai-snake/config`, {
      headers: { Authorization: `Bearer ${token}` },
    });
    expect(read.ok()).toBeTruthy();
    const payload = await read.json() as any;
    expect(payload?.config?.[key]).toBe(value);
  });
});
