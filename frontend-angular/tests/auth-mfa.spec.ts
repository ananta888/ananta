import { test, expect } from '@playwright/test';
import { login, resetAdminMfaState, resetUserAuthStateViaApi, ADMIN_USERNAME, ADMIN_PASSWORD, HUB_URL } from './utils';
import { generate } from 'otplib';

test.describe('MFA Flow', () => {
  test('should enable and disable MFA', async ({ page, request }) => {
    await resetUserAuthStateViaApi(ADMIN_USERNAME, ADMIN_PASSWORD);
    resetAdminMfaState();

    try {
      await login(page);

      const accessToken = await page.evaluate(() => localStorage.getItem('ananta.user.token'));
      if (!accessToken) {
        test.skip(true, 'No bearer token available after login in this environment.');
      }

      const setupRes = await request.post(`${HUB_URL}/mfa/setup`, {
        headers: { Authorization: `Bearer ${accessToken}` }
      });
      if (!setupRes.ok()) {
        test.skip(true, `MFA setup endpoint not available for current auth mode (status ${setupRes.status()}).`);
      }

      const setupPayload = await setupRes.json();
      const setupData = setupPayload?.data ?? setupPayload;
      const secret = String(setupData?.secret || '').trim();
      expect(secret.length).toBeGreaterThan(10);

      const token = String(await generate({ secret }));
      const verifyRes = await request.post(`${HUB_URL}/mfa/verify`, {
        headers: { Authorization: `Bearer ${accessToken}` },
        data: { token }
      });
      expect(verifyRes.ok()).toBeTruthy();

      const verifyPayload = await verifyRes.json();
      const verifyData = verifyPayload?.data ?? verifyPayload;
      const backupCode = String(verifyData?.backup_codes?.[0] || '').trim();
      expect(backupCode.length).toBeGreaterThan(0);

      const mfaRequiredLoginRes = await request.post(`${HUB_URL}/login`, {
        data: { username: ADMIN_USERNAME, password: ADMIN_PASSWORD }
      });
      expect(mfaRequiredLoginRes.ok()).toBeTruthy();
      const mfaRequiredLoginPayload = await mfaRequiredLoginRes.json();
      const mfaRequiredLoginData = mfaRequiredLoginPayload?.data ?? mfaRequiredLoginPayload;
      expect(Boolean(mfaRequiredLoginData?.mfa_required)).toBeTruthy();

      const mfaLoginRes = await request.post(`${HUB_URL}/login`, {
        data: {
          username: ADMIN_USERNAME,
          password: ADMIN_PASSWORD,
          mfa_token: backupCode
        }
      });
      expect(mfaLoginRes.ok()).toBeTruthy();
      const mfaLoginPayload = await mfaLoginRes.json();
      const mfaLoginData = mfaLoginPayload?.data ?? mfaLoginPayload;
      const tokenAfterLogin = String(mfaLoginData?.access_token || '').trim();
      expect(tokenAfterLogin.length).toBeGreaterThan(10);

      const disableRes = await request.post(`${HUB_URL}/mfa/disable`, {
        headers: { Authorization: `Bearer ${tokenAfterLogin}` }
      });
      expect(disableRes.ok()).toBeTruthy();
    } finally {
      await resetUserAuthStateViaApi(ADMIN_USERNAME, ADMIN_PASSWORD);
      resetAdminMfaState();
    }
  });
});
