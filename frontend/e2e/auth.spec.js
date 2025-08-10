import { test, expect } from '@playwright/test';

test.describe.skip('authentication flow', () => {
  test('rejects requests without API key', async ({ request }) => {
    const res = await request.get('/protected');
    expect(res.status()).toBe(401);
  });
});

// TODO: Add cross-browser and stress testing scenarios
