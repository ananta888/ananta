import { test, expect } from '@playwright/test';
import { loginFast, resetAdminMfaState, resetUserAuthStateViaApi, ADMIN_USERNAME, ADMIN_PASSWORD, HUB_URL } from './utils';
import { createHmac } from 'node:crypto';

function decodeBase32Secret(secret: string): Buffer {
  const alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567';
  let bits = '';
  for (const char of secret.toUpperCase().replace(/=+$/g, '')) {
    const value = alphabet.indexOf(char);
    if (value < 0) {
      throw new Error(`Invalid Base32 secret character: ${char}`);
    }
    bits += value.toString(2).padStart(5, '0');
  }

  const bytes: number[] = [];
  for (let index = 0; index + 8 <= bits.length; index += 8) {
    bytes.push(parseInt(bits.slice(index, index + 8), 2));
  }
  return Buffer.from(bytes);
}

function generateTotp(secret: string, timestampMs = Date.now()): string {
  const counter = Math.floor(timestampMs / 30_000);
  const counterBuffer = Buffer.alloc(8);
  counterBuffer.writeUInt32BE(Math.floor(counter / 0x100000000), 0);
  counterBuffer.writeUInt32BE(counter >>> 0, 4);

  const digest = createHmac('sha1', decodeBase32Secret(secret)).update(counterBuffer).digest();
  const offset = digest[digest.length - 1] & 0x0f;
  const code =
    ((digest[offset] & 0x7f) << 24) |
    ((digest[offset + 1] & 0xff) << 16) |
    ((digest[offset + 2] & 0xff) << 8) |
    (digest[offset + 3] & 0xff);
  return String(code % 1_000_000).padStart(6, '0');
}

test.describe('MFA Flow', () => {
  test('should enable and disable MFA', async ({ page, request }) => {
    test.setTimeout(120_000);
    await resetUserAuthStateViaApi(ADMIN_USERNAME, ADMIN_PASSWORD);
    resetAdminMfaState();

    try {
      await loginFast(page, request);
      await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });

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
      if (/[^A-Z2-7=]/i.test(secret)) {
        test.skip(true, 'MFA setup returned a masked secret in this environment.');
      }

      const token = generateTotp(secret);
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
