import { test, expect } from '@playwright/test';
import { PUBLIC_WEBRTC_BASE_URL } from '../src/app/services/public-ananta-endpoints';
import { PUBLIC_OIDC_AUTH_MODE, login } from './utils';

test.skip(PUBLIC_OIDC_AUTH_MODE !== 'oidc-ui', 'Set E2E_AUTH_MODE=oidc-ui to run the public OIDC/WebRTC smoke test.');

test.describe('Public OIDC / WebRTC', () => {
  test('logs in via public Keycloak and can access rendezvous auth endpoints', async ({ page, request }) => {
    test.setTimeout(180_000);

    await login(page);
    await expect(page).toHaveURL(/\/(workspace|dashboard|help)(\/|$)/, { timeout: 60_000 });

    const token = await page.evaluate(() => localStorage.getItem('ananta.oidc.access_token'));
    expect(token, 'Expected the public OIDC access token to be stored in localStorage').toBeTruthy();

    const healthResponse = await request.get(`${PUBLIC_WEBRTC_BASE_URL}/health`);
    expect(healthResponse.ok()).toBeTruthy();

    const turnResponse = await request.get(`${PUBLIC_WEBRTC_BASE_URL}/rendezvous/turn-credentials`, {
      headers: {
        Authorization: `Bearer ${token}`,
      },
    });
    expect(turnResponse.ok()).toBeTruthy();
    const payload = await turnResponse.json();
    expect(payload).toBeTruthy();
  });
});
