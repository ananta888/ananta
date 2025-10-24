import { test, expect } from '@playwright/test';

// Run this suite only when explicitly enabled. The backend currently has no /protected route.
// Enable by setting AUTH_TEST=1 in the environment.
const runAuthSuite = process.env.AUTH_TEST === '1';
const describeAuth = runAuthSuite ? test.describe : test.describe.skip;

describeAuth('authentication flow', () => {
  test('rejects requests without API key', async ({ request }) => {
    const res = await request.get('/protected');
    expect(res.status()).toBe(401);
  });
});

// TODO: Add cross-browser and stress testing scenarios
