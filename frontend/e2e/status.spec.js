import { test, expect } from '@playwright/test';

test('controller status endpoint reachable', async ({ request }) => {
  const res = await request.get('/status');
  expect(res.status()).toBe(200);
});
