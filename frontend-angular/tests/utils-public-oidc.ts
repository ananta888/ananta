import { Page } from '@playwright/test';
import {
  PUBLIC_OIDC_CLIENT_ID as DEFAULT_PUBLIC_OIDC_CLIENT_ID,
  PUBLIC_OIDC_ISSUER as DEFAULT_PUBLIC_OIDC_ISSUER,
  PUBLIC_OIDC_REALM,
} from '../src/app/config/public-oidc.config';

const PUBLIC_OIDC_ISSUER = process.env.E2E_OIDC_ISSUER || DEFAULT_PUBLIC_OIDC_ISSUER;
const PUBLIC_OIDC_CLIENT_ID = process.env.E2E_OIDC_CLIENT_ID || DEFAULT_PUBLIC_OIDC_CLIENT_ID;
const PUBLIC_OIDC_USERNAME = process.env.E2E_OIDC_USERNAME || 'e2e';
const PUBLIC_OIDC_PASSWORD = process.env.E2E_OIDC_PASSWORD || '';
const PUBLIC_OIDC_CLIENT_SECRET = process.env.E2E_OIDC_CLIENT_SECRET || '';
const PUBLIC_OIDC_AUTH_MODE = (process.env.E2E_AUTH_MODE || 'local').toLowerCase();
const PUBLIC_KEYCLOAK_BASE_URL = process.env.E2E_KEYCLOAK_BASE_URL || PUBLIC_OIDC_ISSUER.replace(/\/realms\/.+$/, '');
const PUBLIC_KEYCLOAK_HOST = PUBLIC_KEYCLOAK_BASE_URL.replace(/^https?:\/\//, '');
const PUBLIC_OIDC_REALM_NAME = PUBLIC_OIDC_REALM;
const PUBLIC_OIDC_SCOPES = 'openid profile email';

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function fetchWithTimeout(url: string, init: RequestInit = {}, timeoutMs = 8000): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

export function isPublicOidcUiMode(): boolean {
  return PUBLIC_OIDC_AUTH_MODE === 'oidc-ui';
}

type PublicOidcCredentials = {
  username: string;
  password: string;
  email: string;
  register: boolean;
};

type PublicOidcMetadata = {
  authorization_endpoint: string;
  token_endpoint: string;
  device_authorization_endpoint?: string;
};

type PublicDeviceAuthResponse = {
  device_code: string;
  user_code: string;
  verification_uri: string;
  verification_uri_complete?: string;
  expires_in: number;
  interval: number;
};

function resolvePublicOidcCredentials(): PublicOidcCredentials {
  const explicitPassword = String(process.env.E2E_OIDC_PASSWORD || '').trim();
  const explicitUsername = String(process.env.E2E_OIDC_USERNAME || '').trim();
  if (explicitPassword) {
    const username = explicitUsername || PUBLIC_OIDC_USERNAME;
    return {
      username,
      password: explicitPassword,
      email: username.includes('@') ? username : `${username}@example.invalid`,
      register: false,
    };
  }

  const suffix = `${Date.now().toString(36)}-${randomUUID().slice(0, 8)}`;
  const username = `e2e-${suffix}`;
  const password = `Ananta!${randomUUID().replace(/-/g, '').slice(0, 18)}1`;
  return {
    username,
    password,
    email: `${username}@example.invalid`,
    register: true,
  };
}

async function firstVisibleLocator(page: Page, selectors: string[]): Promise<ReturnType<Page['locator']> | null> {
  for (const selector of selectors) {
    const candidate = page.locator(selector).first();
    if (await candidate.count().catch(() => 0) === 0) continue;
    if (await candidate.isVisible().catch(() => false)) return candidate;
  }
  return null;
}

async function fillFirstVisible(page: Page, selectors: string[], value: string): Promise<boolean> {
  const target = await firstVisibleLocator(page, selectors);
  if (!target) return false;
  await target.fill(value);
  return true;
}

async function clickFirstVisible(page: Page, selectors: string[]): Promise<boolean> {
  const target = await firstVisibleLocator(page, selectors);
  if (!target) return false;
  await target.click();
  return true;
}

async function loadPublicOidcMetadata(issuer = PUBLIC_OIDC_ISSUER): Promise<PublicOidcMetadata> {
  const normalizedIssuer = issuer.replace(/\/$/, '');
  const response = await retryFetch(
    `${normalizedIssuer}/.well-known/openid-configuration`,
    {},
    5,
    500
  );
  if (!response.ok) {
    throw new Error(`OIDC discovery failed for ${normalizedIssuer}: ${response.status}`);
  }
  return response.json() as Promise<PublicOidcMetadata>;
}

async function startPublicOidcDeviceFlow(): Promise<PublicDeviceAuthResponse> {
  const metadata = await loadPublicOidcMetadata();
  const endpoint = metadata.device_authorization_endpoint
    ?? `${PUBLIC_OIDC_ISSUER.replace(/\/$/, '')}/protocol/openid-connect/auth/device`;
  const response = await retryFetch(
    endpoint,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        client_id: PUBLIC_OIDC_CLIENT_ID,
        scope: PUBLIC_OIDC_SCOPES,
      }).toString(),
    },
    5,
    750
  );
  if (!response.ok) {
    throw new Error(`Public OIDC device flow start failed: ${response.status}`);
  }
  return response.json() as Promise<PublicDeviceAuthResponse>;
}

async function pollPublicOidcDeviceToken(deviceCode: string, intervalSec: number): Promise<{ accessToken: string; refreshToken?: string }> {
  const metadata = await loadPublicOidcMetadata();
  const deadlineMs = Number(process.env.E2E_PUBLIC_OIDC_DEVICE_FLOW_TIMEOUT_MS || '180000');
  const started = Date.now();
  let delayMs = Math.max(1000, intervalSec * 1000);

  while ((Date.now() - started) < deadlineMs) {
    const response = await fetchWithTimeout(
      metadata.token_endpoint,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams({
          grant_type: 'urn:ietf:params:oauth:grant-type:device_code',
          client_id: PUBLIC_OIDC_CLIENT_ID,
          device_code: deviceCode,
        }).toString(),
      },
      Math.max(5000, delayMs + 5000)
    );

    if (response.ok) {
      const tokens = await response.json() as any;
      const accessToken = String(tokens?.access_token || '').trim();
      if (!accessToken) {
        throw new Error('Public OIDC device flow returned no access token');
      }
      return {
        accessToken,
        refreshToken: tokens?.refresh_token ? String(tokens.refresh_token) : undefined,
      };
    }

    if (response.status === 400) {
      const payload = await response.json().catch(() => ({})) as any;
      const error = String(payload?.error || '').trim();
      if (error === 'authorization_pending') {
        await sleep(delayMs);
        continue;
      }
      if (error === 'slow_down') {
        delayMs += 5000;
        await sleep(delayMs);
        continue;
      }
      throw new Error(`Public OIDC device flow failed: ${error || response.status}`);
    }

    if (response.status >= 500 || response.status === 429) {
      await sleep(delayMs);
      delayMs = Math.min(delayMs + 2000, 15000);
      continue;
    }

    throw new Error(`Public OIDC device flow token poll failed: ${response.status}`);
  }

  throw new Error('Public OIDC device flow timed out');
}

async function waitForPublicOidcToken(page: Page, timeoutMs = 60_000): Promise<void> {
  await expect.poll(
    async () => page.evaluate(() => localStorage.getItem('ananta.user.token')),
    { timeout: timeoutMs }
  ).toBeTruthy();
}

async function waitForPublicOidcAccessToken(page: Page, timeoutMs = 60_000): Promise<void> {
  await expect.poll(
    async () => page.evaluate(() => localStorage.getItem('ananta.oidc.access_token')),
    { timeout: timeoutMs }
  ).toBeTruthy();
}

export async function loginViaPublicOidcUi(page: Page): Promise<void> {
  const creds = resolvePublicOidcCredentials();
  const keycloakLogin = page.getByRole('button', { name: /Mit Keycloak anmelden/i });
  await expect(keycloakLogin).toBeVisible({ timeout: 30_000 });
  await keycloakLogin.click();

  await page.waitForURL(/keycloak\.ananta\.de\/realms\/ananta-e2e\//i, { timeout: 120_000 });
  await page.waitForLoadState('domcontentloaded').catch(() => undefined);

  if (await page.getByText(/Invalid parameter: redirect_uri/i).isVisible().catch(() => false)) {
    throw new Error(
      'Keycloak rejected redirect_uri. Add http://angular-frontend:4200/oidc-callback to the ' +
      'valid redirect URIs of client ananta-tui in realm ananta-e2e.'
    );
  }

  if (creds.register) {
    const registerLink = await firstVisibleLocator(page, [
      '#kc-registration',
      'a[href*="registration"]',
      'a[href*="register"]',
      'a:has-text("Register")',
      'a:has-text("Create account")',
      'a:has-text("Registrieren")',
    ]);
    if (registerLink) {
      await registerLink.click();
      await page.waitForLoadState('domcontentloaded').catch(() => undefined);
    }
  }

  const filledUsername = await fillFirstVisible(page, ['#username', 'input[name="username"]', 'input[type="text"]'], creds.username);
  const filledPassword = await fillFirstVisible(page, ['#password', 'input[name="password"]', 'input[type="password"]'], creds.password);
  if (!filledUsername || !filledPassword) {
    throw new Error('Keycloak login form is missing username/password fields');
  }
  await fillFirstVisible(page, ['#email', 'input[name="email"]', 'input[type="email"]'], creds.email);
  await fillFirstVisible(page, ['#firstName', 'input[name="firstName"]'], 'E2E');
  await fillFirstVisible(page, ['#lastName', 'input[name="lastName"]'], 'Runner');
  await fillFirstVisible(page, ['#password-confirm', 'input[name="password-confirm"]', 'input[name="password-confirmation"]'], creds.password);

  const submitted = await clickFirstVisible(page, [
    '#kc-login',
    '#kc-register',
    'button[type="submit"]',
    'input[type="submit"]',
    'button:has-text("Sign in")',
    'button:has-text("Anmelden")',
    'button:has-text("Login")',
  ]);
  if (!submitted) {
    throw new Error('Keycloak submit button not found in public OIDC flow');
  }

  await expect.poll(
    async () => page.evaluate(() => localStorage.getItem('ananta.user.token')),
    { timeout: 180_000 }
  ).toBeTruthy();
  await waitForPublicOidcAccessToken(page, 180_000);

