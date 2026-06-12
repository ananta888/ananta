import { expect, Page, type APIRequestContext, type APIResponse } from '@playwright/test';
import { randomUUID } from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';
import {
  PUBLIC_KEYCLOAK_BASE_URL,
  PUBLIC_OIDC_CLIENT_ID as DEFAULT_PUBLIC_OIDC_CLIENT_ID,
  PUBLIC_OIDC_ISSUER as DEFAULT_PUBLIC_OIDC_ISSUER,
  PUBLIC_OIDC_REALM,
} from '../src/app/services/public-ananta-endpoints';

import { isPublicOidcUiMode, loginViaPublicOidcUi } from './utils-public-oidc';
export { createJourneyCleanupPolicy, type JourneyCleanupPolicy } from './utils-cleanup-policy';
export const ADMIN_USERNAME = process.env.E2E_ADMIN_USER || 'admin';
export const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'test123';
export const HUB_URL = process.env.E2E_HUB_URL || 'http://127.0.0.1:5500';
export const ALPHA_URL = process.env.E2E_ALPHA_URL || 'http://127.0.0.1:5501';
export const BETA_URL = process.env.E2E_BETA_URL || 'http://127.0.0.1:5502';
export const PUBLIC_OIDC_ISSUER = process.env.E2E_OIDC_ISSUER || DEFAULT_PUBLIC_OIDC_ISSUER;
export const PUBLIC_OIDC_CLIENT_ID = process.env.E2E_OIDC_CLIENT_ID || DEFAULT_PUBLIC_OIDC_CLIENT_ID;
export const PUBLIC_OIDC_USERNAME = process.env.E2E_OIDC_USERNAME || 'e2e';
export const PUBLIC_OIDC_PASSWORD = process.env.E2E_OIDC_PASSWORD || '';
export const PUBLIC_OIDC_CLIENT_SECRET = process.env.E2E_OIDC_CLIENT_SECRET || '';
export const PUBLIC_OIDC_AUTH_MODE = (process.env.E2E_AUTH_MODE || 'local').toLowerCase();
export const PUBLIC_KEYCLOAK_HOST = PUBLIC_KEYCLOAK_BASE_URL.replace(/^https?:\/\//, '');
export const PUBLIC_OIDC_REALM_NAME = PUBLIC_OIDC_REALM;
export const USE_EXISTING_SERVICES =
  process.env.ANANTA_E2E_USE_EXISTING === '1' || process.env.E2E_REUSE_SERVER === '1';
export const TEST_LOGIN_IP = USE_EXISTING_SERVICES ? undefined : '127.0.0.1';
export const HUB_AGENT_TOKEN = process.env.E2E_HUB_AGENT_TOKEN || process.env.AGENT_TOKEN_HUB || 'generate_a_random_token_for_hub';
export const ALPHA_AGENT_TOKEN = process.env.E2E_ALPHA_AGENT_TOKEN || process.env.AGENT_TOKEN_ALPHA || 'generate_a_random_token_for_alpha';
export const BETA_AGENT_TOKEN = process.env.E2E_BETA_AGENT_TOKEN || process.env.AGENT_TOKEN_BETA || 'generate_a_random_token_for_beta';
const ENABLE_DETERMINISTIC_SCRUM_SEED = process.env.E2E_DETERMINISTIC_SCRUM_SEED === '1';
const E2E_SCRUM_SEED_TEAM_NAME = process.env.E2E_SCRUM_SEED_TEAM_NAME || 'E2E Seed Scrum Team';
let hubHealthReady = false;
let hubHealthWarningLogged = false;
let deterministicScrumSeedReady = false;
let deterministicScrumSeedInFlight: Promise<void> | null = null;
type BrowserGuardState = {
  consoleErrors: string[];
  pageErrors: string[];
};
const browserGuardState = new WeakMap<Page, BrowserGuardState>();

function shouldIgnoreConsoleError(text: string): boolean {
  if (!text) return true;
  if (text.includes('favicon.ico')) return true;
  if (text.includes('Failed to load resource: the server responded with a status of 401')) return true;
  if (text.includes('ExpressionChangedAfterItHasBeenCheckedError') && text.includes('toast-success')) return true;
  return false;
}

export function attachBrowserErrorGuards(page: Page): void {
  if (browserGuardState.has(page)) return;
  const state: BrowserGuardState = { consoleErrors: [], pageErrors: [] };
  browserGuardState.set(page, state);

  page.on('console', (msg) => {
    if (msg.type() !== 'error') return;
    const text = msg.text().trim();
    if (shouldIgnoreConsoleError(text)) return;
    state.consoleErrors.push(text);
  });

  page.on('pageerror', (err) => {
    const text = (err?.message || String(err)).trim();
    if (!text) return;
    state.pageErrors.push(text);
  });
}

export function clearBrowserErrorGuards(page: Page): void {
  const state = browserGuardState.get(page);
  if (!state) return;
  state.consoleErrors.length = 0;
  state.pageErrors.length = 0;
}

export async function assertNoUnhandledBrowserErrors(page: Page): Promise<void> {
  const state = browserGuardState.get(page);
  expect(state?.consoleErrors || [], `Console errors:\n${(state?.consoleErrors || []).join('\n')}`).toEqual([]);
  expect(state?.pageErrors || [], `Page errors:\n${(state?.pageErrors || []).join('\n')}`).toEqual([]);
}

export async function assertErrorOverlaysInViewport(page: Page): Promise<void> {
  const viewport = page.viewportSize();
  if (!viewport) return;

  const overlays = page.locator('.notification.error, .toast.toast-error');
  const count = await overlays.count();
  for (let i = 0; i < count; i += 1) {
    const item = overlays.nth(i);
    if (!(await item.isVisible().catch(() => false))) continue;
    const box = await item.boundingBox();
    if (!box) continue;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.y).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isPlaceholderAgentToken(token: string): boolean {
  return /^generate_a_random_token_for_/.test(String(token || '').trim());
}

function canUseAgentToken(token: string): boolean {
  const value = String(token || '').trim();
  return value.length > 0 && !isPlaceholderAgentToken(value);
}

  // Do NOT use page.goto() here: the OIDC redirect uses the Docker hostname
  // (angular-frontend:4200) but Playwright's baseURL uses the resolved IP.
  // Both are different localStorage origins, so a page.goto() to the base URL
  // would land on empty localStorage and the auth guard would redirect to /login.
  // Instead, wait for Angular's router to naturally navigate after the callback.
  await page.waitForURL(/\/(workspace|dashboard|help)(\/|$)/, { timeout: 60_000 }).catch(() => undefined);
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

async function retryFetch(url: string, init: RequestInit, attempts = 4, delayMs = 500): Promise<Response> {
  let lastError: unknown;
  for (let i = 0; i < attempts; i += 1) {
    try {
      return await fetchWithTimeout(url, init, 8000);
    } catch (err) {
      lastError = err;
      if (i < attempts - 1) {
        await sleep(delayMs * (i + 1));
      }
    }
  }
  throw lastError instanceof Error ? lastError : new Error('fetch failed');
}

async function waitForHub(): Promise<boolean> {
  if (USE_EXISTING_SERVICES) {
    hubHealthReady = true;
    return true;
  }
  if (hubHealthReady) return true;
  const maxWaitMs = Number(process.env.E2E_HUB_WAIT_MS || '30000');
  const probeTimeoutMs = Number(process.env.E2E_HUB_PROBE_TIMEOUT_MS || '2500');
  const probeIntervalMs = Number(process.env.E2E_HUB_PROBE_INTERVAL_MS || '400');
  const started = Date.now();

  while ((Date.now() - started) < maxWaitMs) {
    let timeoutId: NodeJS.Timeout | undefined;
    try {
      const controller = new AbortController();
      timeoutId = setTimeout(() => controller.abort(), probeTimeoutMs);
      const res = await fetch(`${HUB_URL}/health`, { signal: controller.signal });
      if (res.ok) {
        hubHealthReady = true;
        return true;
      }
    } catch {}
    finally {
      if (timeoutId) clearTimeout(timeoutId);
    }
    await sleep(probeIntervalMs);
  }
  // One final relaxed probe helps avoid false negatives under heavy startup load.
  try {
    const res = await fetch(`${HUB_URL}/health`);
    if (res.ok) {
      hubHealthReady = true;
      return true;
    }
  } catch {}
  return false;
}

async function loginViaApi(
  username: string,
  password: string
): Promise<{ accessToken?: string; refreshToken?: string; mfaRequired?: boolean } | null> {
  const attempts = Number(process.env.E2E_API_LOGIN_RETRIES || '10');
  const timeoutMs = Number(process.env.E2E_API_LOGIN_TIMEOUT_MS || '15000');
  for (let i = 0; i < attempts; i += 1) {
    try {
      const res = await fetchWithTimeout(`${HUB_URL}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      }, timeoutMs);
      if (res.ok) {
        const payload = await res.json() as any;
        const data = payload?.data || payload;
        const accessToken = data?.access_token;
        if (accessToken) {
          return { accessToken, refreshToken: data?.refresh_token };
        }
        if (data?.mfa_required) {
          return { mfaRequired: true };
        }
      }
    } catch {}
    await sleep(300);
  }
  return null;
}

async function normalizeExistingAdminAuthState(username: string, password: string): Promise<void> {
  if (!USE_EXISTING_SERVICES || username !== ADMIN_USERNAME) return;
  for (const candidate of adminPasswordCandidates(password)) {
    try {
      if (await resetUserAuthStateViaApi(username, candidate)) break;
    } catch {}
  }
  try { await ensureLoginAttemptsCleared(); } catch {}
}

function adminPasswordCandidates(preferredPassword: string): string[] {
  return [String(preferredPassword || '').trim()].filter(Boolean);
}

async function provisionUserViaTestApi(
  username: string,
  password: string,
  role: 'admin' | 'user' = 'user'
): Promise<boolean> {
  try {
    const res = await fetchWithTimeout(`${HUB_URL}/test/provision-user`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role, overwrite: true })
    }, Number(process.env.E2E_API_AUX_TIMEOUT_MS || '8000'));
    return res.ok;
  } catch {
    return false;
  }
}

async function deleteUserViaTestApi(username: string): Promise<boolean> {
  try {
    const res = await fetchWithTimeout(`${HUB_URL}/test/users/${encodeURIComponent(username)}`, {
      method: 'DELETE'
    }, Number(process.env.E2E_API_AUX_TIMEOUT_MS || '8000'));
    return res.ok || res.status === 404;
  } catch {
    return false;
  }
}

export async function resetUserAuthStateViaApi(username: string, password?: string): Promise<boolean> {
  try {
    const body: any = { username };
    if (password) body.password = password;
    const res = await fetchWithTimeout(`${HUB_URL}/test/reset-user-auth-state`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    }, Number(process.env.E2E_API_AUX_TIMEOUT_MS || '8000'));
    return res.ok;
  } catch {
    return false;
  }
}

export async function prepareLoginPage(page: Page) {
  attachBrowserErrorGuards(page);
  const hubReady = await waitForHub();
  if (!hubReady && !hubHealthWarningLogged) {
    hubHealthWarningLogged = true;
    console.warn(`Hub healthcheck still failing for ${HUB_URL}; continuing with login page setup.`);
  }
  await page.goto('/login', { waitUntil: 'domcontentloaded' });
  await page.evaluate(({ hubUrl, alphaUrl, betaUrl, hubToken, alphaToken, betaToken }) => {
    localStorage.clear();
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', url: hubUrl, token: hubToken, role: 'hub' },
      { name: 'alpha', url: alphaUrl, token: alphaToken, role: 'worker' },
      { name: 'beta', url: betaUrl, token: betaToken, role: 'worker' }
    ]));
    localStorage.setItem('ananta.shell.mode', 'advanced');
  }, { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL, hubToken: HUB_AGENT_TOKEN, alphaToken: ALPHA_AGENT_TOKEN, betaToken: BETA_AGENT_TOKEN });
  await page.reload({ waitUntil: 'domcontentloaded' });
}

export async function openTeamsAdminStudio(page: Page) {
  await page.goto('/teams', { waitUntil: 'domcontentloaded' });
  await expect(page.getByText(/Blueprint-first Teams/i)).toBeVisible({ timeout: 20_000 });
  const adminModeButton = page.getByRole('button', { name: /Admin-\/Studio-Modus/i });
  await expect(adminModeButton).toBeVisible({ timeout: 20_000 });
  await adminModeButton.click();
  await expect(page.locator('.teams-editor-panel')).toBeVisible({ timeout: 30_000 });
}

export async function login(page: Page, username = ADMIN_USERNAME, password = ADMIN_PASSWORD) {
  attachBrowserErrorGuards(page);
  // Prevent cross-test bleed from IP-based login throttling.
  try { clearLoginAttempts('127.0.0.1'); } catch {}
  try { await ensureLoginAttemptsCleared(); } catch {}
  if (isPublicOidcUiMode()) {
    await prepareLoginPage(page);
    await loginViaPublicOidcUi(page);
    return;
  }
  // Try to normalize admin auth state in shared compose-lite runs.
  await normalizeExistingAdminAuthState(username, password);
  await prepareLoginPage(page);
  const dashboard = page.getByRole('heading', { name: /System Dashboard|Ananta starten/i });
  const passwordCandidates = username === ADMIN_USERNAME ? adminPasswordCandidates(password) : [password];

  if (USE_EXISTING_SERVICES && username === ADMIN_USERNAME) {
    if (!canUseAgentToken(HUB_AGENT_TOKEN)) {
      throw new Error('Login fallback requested but HUB agent token is a placeholder');
    }
    const token = await getAccessToken(username, password).catch(() => HUB_AGENT_TOKEN);
    await page.evaluate(({ hubUrl, alphaUrl, betaUrl, hubToken, alphaToken, betaToken, token }) => {
      localStorage.setItem('ananta.agents.v1', JSON.stringify([
        { name: 'hub', url: hubUrl, token: hubToken, role: 'hub' },
        { name: 'alpha', url: alphaUrl, token: alphaToken, role: 'worker' },
        { name: 'beta', url: betaUrl, token: betaToken, role: 'worker' }
      ]));
      localStorage.setItem('ananta.user.token', token);
      localStorage.setItem('ananta.shell.mode', 'advanced');
    }, { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL, hubToken: HUB_AGENT_TOKEN, alphaToken: ALPHA_AGENT_TOKEN, betaToken: BETA_AGENT_TOKEN, token });
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(dashboard).toBeVisible({ timeout: 30000 });
    return;
  }

  // Prefer API login to reduce UI bootstrap flakes on slow startup.
  let apiMfaRequired = false;
  for (const candidate of passwordCandidates) {
    const apiLogin = await loginViaApi(username, candidate);
    if (apiLogin?.accessToken) {
      await page.evaluate(({ hubUrl, alphaUrl, betaUrl, hubToken, alphaToken, betaToken, token, refreshToken }) => {
        localStorage.setItem('ananta.agents.v1', JSON.stringify([
          { name: 'hub', url: hubUrl, token: hubToken, role: 'hub' },
          { name: 'alpha', url: alphaUrl, token: alphaToken, role: 'worker' },
          { name: 'beta', url: betaUrl, token: betaToken, role: 'worker' }
        ]));
        localStorage.setItem('ananta.user.token', token);
        if (refreshToken) localStorage.setItem('ananta.user.refresh_token', refreshToken);
        localStorage.setItem('ananta.shell.mode', 'advanced');
      }, { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL, hubToken: HUB_AGENT_TOKEN, alphaToken: ALPHA_AGENT_TOKEN, betaToken: BETA_AGENT_TOKEN, token: apiLogin.accessToken, refreshToken: apiLogin.refreshToken });
      await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
      await expect(dashboard).toBeVisible({ timeout: 30000 });
      return;
    }
    if (apiLogin?.mfaRequired) {
      apiMfaRequired = true;
      break;
    }
  }

  if (apiMfaRequired) {
    if (!canUseAgentToken(HUB_AGENT_TOKEN)) {
      throw new Error('MFA fallback requested but HUB agent token is a placeholder');
    }
    await page.evaluate(({ hubUrl, alphaUrl, betaUrl, hubToken, alphaToken, betaToken }) => {
      localStorage.setItem('ananta.agents.v1', JSON.stringify([
        { name: 'hub', url: hubUrl, token: hubToken, role: 'hub' },
        { name: 'alpha', url: alphaUrl, token: alphaToken, role: 'worker' },
        { name: 'beta', url: betaUrl, token: betaToken, role: 'worker' }
      ]));
      localStorage.setItem('ananta.user.token', hubToken);
      localStorage.setItem('ananta.shell.mode', 'advanced');
    }, { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL, hubToken: HUB_AGENT_TOKEN, alphaToken: ALPHA_AGENT_TOKEN, betaToken: BETA_AGENT_TOKEN });
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(dashboard).toBeVisible({ timeout: 30000 });
    return;
  }

  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(passwordCandidates[0] || password);

  const submit = page.locator('button.primary');
  const error = page.locator('.error-msg');

  const maxAttempts = Number(process.env.E2E_LOGIN_RETRY_ATTEMPTS || '4');
  const loginAttemptTimeoutMs = Number(process.env.E2E_LOGIN_ATTEMPT_TIMEOUT_MS || '12000');
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    const attemptPassword = passwordCandidates[attempt % passwordCandidates.length] || password;
    try {
      await page.locator('input[name="password"]').fill(attemptPassword);
      await expect(submit).toBeEnabled({ timeout: 5000 });
      await submit.click();
      await expect(dashboard).toBeVisible({ timeout: loginAttemptTimeoutMs });
      return;
    } catch (e: any) {
      console.warn(`Login attempt ${attempt + 1} failed: ${e.message}`);
      const pageClosed = page.isClosed() || page.context().pages().length === 0;
      if (!pageClosed) {
        try {
          if (await error.isVisible()) {
            console.warn(`Error message visible: ${await error.innerText()}`);
          }
        } catch {}
      }
      // Give API/auth middleware a short cooldown before retrying.
      await sleep(300);
      if (page.isClosed()) break;
      await page.reload({ waitUntil: 'domcontentloaded' });
      await page.locator('input[name="username"]').fill(username);
      await page.locator('input[name="password"]').fill(attemptPassword);
    }
  }

  if (USE_EXISTING_SERVICES && username === ADMIN_USERNAME) {
    if (!canUseAgentToken(HUB_AGENT_TOKEN)) {
      throw new Error('Login fallback requested but HUB agent token is a placeholder');
    }
    if (page.isClosed()) {
      throw new Error('Login page closed before token fallback could run');
    }
    await page.evaluate(({ hubUrl, alphaUrl, betaUrl, hubToken, alphaToken, betaToken }) => {
      localStorage.setItem('ananta.agents.v1', JSON.stringify([
        { name: 'hub', url: hubUrl, token: hubToken, role: 'hub' },
        { name: 'alpha', url: alphaUrl, token: alphaToken, role: 'worker' },
        { name: 'beta', url: betaUrl, token: betaToken, role: 'worker' }
      ]));
      localStorage.setItem('ananta.user.token', hubToken);
      localStorage.setItem('ananta.shell.mode', 'advanced');
    }, { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL, hubToken: HUB_AGENT_TOKEN, alphaToken: ALPHA_AGENT_TOKEN, betaToken: BETA_AGENT_TOKEN });
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  }

  await expect(dashboard).toBeVisible({ timeout: 30000 });
}

export async function loginFast(
  page: Page,
  request: APIRequestContext,
  username = ADMIN_USERNAME,
  password = ADMIN_PASSWORD
) {
  attachBrowserErrorGuards(page);
  try { await ensureLoginAttemptsCleared(TEST_LOGIN_IP); } catch {}
  await normalizeExistingAdminAuthState(username, password);
  await prepareLoginPage(page);
  const passwordCandidates = username === ADMIN_USERNAME ? adminPasswordCandidates(password) : [password];
  let accessToken: string | undefined;
  let refreshToken: string | undefined;

  for (const candidate of passwordCandidates) {
    const apiLogin = await loginViaApi(username, candidate);
    if (!apiLogin?.accessToken) continue;
    accessToken = apiLogin.accessToken;
    refreshToken = apiLogin.refreshToken;
    break;
  }

  if (!accessToken) {
    for (const candidate of passwordCandidates) {
      const response = await request.post(`${HUB_URL}/login`, {
        timeout: Number(process.env.E2E_API_LOGIN_TIMEOUT_MS || '45000'),
        failOnStatusCode: false,
        data: { username, password: candidate },
      });
      const payload = await response.json().catch(() => ({})) as any;
      accessToken = payload?.data?.access_token;
      refreshToken = payload?.data?.refresh_token;
      if (accessToken) break;
    }
  }

  if (!accessToken && USE_EXISTING_SERVICES && username === ADMIN_USERNAME && canUseAgentToken(HUB_AGENT_TOKEN)) {
    accessToken = HUB_AGENT_TOKEN;
  }

  expect(accessToken).toBeTruthy();
  if (!accessToken) {
    throw new Error(`No access token returned for ${username}`);
  }

  await ensureDeterministicScrumSeed(accessToken);

  await page.evaluate(
    ({ hubUrl, alphaUrl, betaUrl, hubToken, alphaToken, betaToken, token, refreshToken }) => {
      localStorage.clear();
      localStorage.setItem(
        'ananta.agents.v1',
        JSON.stringify([
          { name: 'hub', url: hubUrl, token: hubToken, role: 'hub' },
          { name: 'alpha', url: alphaUrl, token: alphaToken, role: 'worker' },
          { name: 'beta', url: betaUrl, token: betaToken, role: 'worker' },
        ])
      );
      localStorage.setItem('ananta.user.token', token);
      localStorage.setItem('ananta.shell.mode', 'advanced');
      if (refreshToken) {
        localStorage.setItem('ananta.user.refresh_token', refreshToken);
      }
    },
    {
      hubUrl: HUB_URL,
      alphaUrl: ALPHA_URL,
      betaUrl: BETA_URL,
      hubToken: HUB_AGENT_TOKEN,
      alphaToken: ALPHA_AGENT_TOKEN,
      betaToken: BETA_AGENT_TOKEN,
      token: accessToken,
      refreshToken,
    }
  );

  // Re-bootstrap the Angular app after token injection so auth services read the new storage state.
  await page.reload({ waitUntil: 'domcontentloaded' });
  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });

  const dashboardHeading = page.getByRole('heading', { name: /System Dashboard|Ananta starten/i });
  const appNav = page.locator('.app-nav');
  const loginForm = page.locator('input[name="username"]');

  await expect.poll(async () => {
    const url = page.url();
    const hasDashboard = await dashboardHeading.isVisible().catch(() => false);
    const hasNav = await appNav.isVisible().catch(() => false);
    const hasLogin = await loginForm.isVisible().catch(() => false);
    if (hasDashboard || hasNav) return 'authenticated';
    if (/\/login(?:[?#]|$)/.test(url) || hasLogin) return 'login';
    return 'pending';
  }, { timeout: 30000, intervals: [500, 1000, 2000] }).toBe('authenticated');

  if (username === ADMIN_USERNAME) {
    await assertAdminSession(request, String(accessToken));
  }
}

export async function waitForHeaderRole(page: Page, role: string): Promise<void> {
  const headerUser = page.locator('.app-header-user');
  await expect.poll(async () => {
    const text = await headerUser.textContent().catch(() => '');
    return String(text || '');
  }, { timeout: 30000, intervals: [500, 1000, 2000] }).toContain(`(${role})`);
}

async function assertAdminSession(request: APIRequestContext, accessToken: string): Promise<void> {
  const response = await request.get(`${HUB_URL}/me`, {
    headers: {
      Authorization: `Bearer ${accessToken}`,
    },
    timeout: Number(process.env.E2E_API_LOGIN_TIMEOUT_MS || '45000'),
    failOnStatusCode: false,
  });
  expect(response.ok(), `/me returned ${response.status()}`).toBeTruthy();
  const payload = await response.json().catch(() => ({})) as any;
  const data = payload?.data || payload;
  expect(String(data?.role || '').toLowerCase()).toBe('admin');
  expect(String(data?.username || '').trim()).toBe(ADMIN_USERNAME);
}

const RETRYABLE_API_STATUSES = new Set([408, 409, 423, 425, 429, 500, 502, 503, 504]);
const IDEMPOTENT_REQUEST_METHODS = new Set<RequestMethod>(['get', 'delete']);

type RequestMethod = 'get' | 'post' | 'patch' | 'delete';

type RequestWithRetryOptions = {
  headers?: Record<string, string>;
  data?: any;
  attempts?: number;
  timeoutMs?: number;
  retryOnStatuses?: number[];
};

export async function requestWithRetry(
  request: APIRequestContext,
  method: RequestMethod,
  url: string,
  options: RequestWithRetryOptions = {},
): Promise<APIResponse> {
  const isIdempotent = IDEMPOTENT_REQUEST_METHODS.has(method);
  const attempts = Math.max(1, options.attempts ?? (isIdempotent ? Number(process.env.E2E_API_REQUEST_RETRIES || '4') : 1));
  const timeoutMs = Math.max(1, options.timeoutMs ?? Number(process.env.E2E_API_REQUEST_TIMEOUT_MS || '30000'));
  const retryStatuses = new Set(options.retryOnStatuses ?? [...RETRYABLE_API_STATUSES]);
  let lastError: unknown;

  for (let index = 0; index < attempts; index += 1) {
    try {
      const response = await request[method](url, {
        headers: options.headers,
        data: options.data,
        timeout: timeoutMs,
        failOnStatusCode: false,
      } as any);
      if (response.ok()) {
        return response;
      }
      if (!retryStatuses.has(response.status()) || index === attempts - 1) {
        return response;
      }
    } catch (error) {
      lastError = error;
      if (index === attempts - 1) {
        throw error;
      }
    }
    await sleep(Math.min(300 * (index + 1), 1500));
  }

  throw lastError instanceof Error ? lastError : new Error(`requestWithRetry failed: ${method.toUpperCase()} ${url}`);
}

async function postJson(url: string, body: any, token?: string): Promise<Response> {
  const headers: Record<string, string> = { 'Content-Type': 'application/json' };
  if (token) headers.Authorization = `Bearer ${token}`;
  return fetch(url, {
    method: 'POST',
    headers,
    body: JSON.stringify(body)
  });
}

async function apiRequestWithRetry(
  method: 'GET' | 'POST' | 'DELETE' | 'PATCH',
  url: string,
  token: string | null,
  body?: any,
  attempts = 5,
): Promise<Response | null> {
  let lastError: unknown;
  for (let i = 0; i < attempts; i += 1) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const res = await fetch(url, {
        method,
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: method === 'GET' || method === 'DELETE' ? undefined : JSON.stringify(body ?? {}),
        signal: controller.signal,
      });
      return res;
    } catch (err) {
      lastError = err;
      if (i < attempts - 1) {
        await sleep(300 * (i + 1));
      }
    } finally {
      clearTimeout(timer);
    }
  }
  console.warn(`apiRequestWithRetry failed for ${method} ${url}: ${String((lastError as any)?.message || lastError)}`);
  return null;
}

async function unwrapJson(res: Response | null): Promise<any> {
  if (!res) return null;
  try {
    return await res.json();
  } catch {
    return null;
  }
}

function unwrapList(body: any): any[] {
  if (Array.isArray(body)) return body;
  if (Array.isArray(body?.data)) return body.data;
  if (Array.isArray(body?.items)) return body.items;
  return [];
}

async function ensureDeterministicScrumSeedInternal(token: string | null): Promise<void> {
  if (!ENABLE_DETERMINISTIC_SCRUM_SEED || deterministicScrumSeedReady) return;

  const seedRes = await apiRequestWithRetry('POST', `${HUB_URL}/teams/setup-scrum`, token, { name: E2E_SCRUM_SEED_TEAM_NAME });
  if (!seedRes?.ok) {
    const status = seedRes?.status || 'unknown';
    const payload = await unwrapJson(seedRes);
    const teamsRes = await apiRequestWithRetry('GET', `${HUB_URL}/teams`, token, undefined, 2);
    const teamsBody = await unwrapJson(teamsRes);
    const teams = unwrapList(teamsBody);
    const exists = teams.some((t: any) => String(t?.name || '').trim() === E2E_SCRUM_SEED_TEAM_NAME);
    if (!exists) {
      throw new Error(`deterministic scrum seed failed: status=${status} body=${JSON.stringify(payload)}`);
    }
  }
  deterministicScrumSeedReady = true;
}

export async function ensureDeterministicScrumSeed(token: string | null): Promise<void> {
  if (!ENABLE_DETERMINISTIC_SCRUM_SEED || deterministicScrumSeedReady) return;
  if (!deterministicScrumSeedInFlight) {
    deterministicScrumSeedInFlight = ensureDeterministicScrumSeedInternal(token)
      .catch((err) => {
        deterministicScrumSeedReady = false;
        throw err;
      })
      .finally(() => {
        deterministicScrumSeedInFlight = null;
      });
  }
  await deterministicScrumSeedInFlight;
}

export async function getAccessToken(username: string, password: string): Promise<string> {
  await normalizeExistingAdminAuthState(username, password);
  const passwordCandidates = username === ADMIN_USERNAME ? adminPasswordCandidates(password) : [password];

  for (const candidate of passwordCandidates) {
    const apiLogin = await loginViaApi(username, candidate);
    if (apiLogin?.accessToken) return apiLogin.accessToken;
  }

  try { await ensureLoginAttemptsCleared(); } catch {}

  for (const candidate of passwordCandidates) {
    const res = await postJson(`${HUB_URL}/login`, { username, password: candidate });
    if (!res.ok) {
      if (res.status === 429) {
        try { await ensureLoginAttemptsCleared(); } catch {}
        continue;
      }
      continue;
    }
    const payload = await res.json() as any;
    const token = payload?.data?.access_token;
    if (token) return token;
  }

  if (USE_EXISTING_SERVICES && username === ADMIN_USERNAME && canUseAgentToken(HUB_AGENT_TOKEN)) {
    return HUB_AGENT_TOKEN;
  }

  throw new Error(`No access token returned for ${username}`);
}

export async function createUserAsAdmin(username: string, password: string, role: 'admin' | 'user' = 'user') {
  if (USE_EXISTING_SERVICES && (await provisionUserViaTestApi(username, password, role))) {
    return;
  }
  const adminToken = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);
  const res = await postJson(`${HUB_URL}/users`, { username, password, role }, adminToken);
  if (!res.ok) {
    throw new Error(`Create user failed (${username}): ${res.status}`);
  }
}

export async function deleteUserAsAdmin(username: string) {
  if (USE_EXISTING_SERVICES && (await deleteUserViaTestApi(username))) {
    return;
  }
  try {
    const adminToken = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);
    const res = await retryFetch(`${HUB_URL}/users/${encodeURIComponent(username)}`, {
      method: 'DELETE',
      headers: { Authorization: `Bearer ${adminToken}` }
    }, Number(process.env.E2E_DELETE_USER_RETRIES || '5'));
    if (!res.ok && res.status !== 404) {
      throw new Error(`Delete user failed (${username}): ${res.status}`);
    }
  } catch (err) {
    if (USE_EXISTING_SERVICES) {
      // Best-effort cleanup in shared/existing environments to avoid false-negative test runs.
      console.warn(`Cleanup warning (delete user ${username}): ${String((err as any)?.message || err)}`);
      return;
    }
    throw err;
  }
}

function getTestDbPath() {
  return path.resolve(__dirname, '..', '..', 'data_test_e2e', 'hub', 'ananta.db');
}

function runSqliteScript(script: string, args: string[]) {
  const dbPath = getTestDbPath();
  if (!fs.existsSync(dbPath)) {
    if (USE_EXISTING_SERVICES) {
      return;
    }
    throw new Error(`E2E database not found: ${dbPath}`);
  }
  execFileSync(resolvePythonBinary(), ['-c', script, dbPath, ...args], { stdio: 'ignore' });
}

function resolvePythonBinary(): string {
  const explicit = process.env.PYTHON_BIN?.trim();
  if (explicit) return explicit;

  const repoVenvPython = path.resolve(__dirname, '..', '..', '.venv', 'bin', 'python3');
  if (fs.existsSync(repoVenvPython)) return repoVenvPython;

  for (const candidate of ['python3', 'python']) {
    const probe = spawnSync(candidate, ['--version'], { stdio: 'ignore' });
    if (probe.status === 0) return candidate;
  }

  return 'python3';
}

export function resetAdminMfaState(username: string = ADMIN_USERNAME) {
  const script = `
import json, sqlite3, sys
db_path, user = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("UPDATE users SET mfa_enabled = 0, mfa_secret = NULL, mfa_backup_codes = ?, failed_login_attempts = 0, lockout_until = NULL WHERE username = ?", (json.dumps([]), user))
conn.commit()
conn.close()
`;
  runSqliteScript(script, [username]);
}

export function resetAdminPassword(username: string = ADMIN_USERNAME, password: string = ADMIN_PASSWORD) {
  const script = `
import sqlite3, sys
from werkzeug.security import generate_password_hash
db_path, user, pwd = sys.argv[1], sys.argv[2], sys.argv[3]
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("UPDATE users SET password_hash = ?, failed_login_attempts = 0, lockout_until = NULL WHERE username = ?", (generate_password_hash(pwd), user))
cur.execute("DELETE FROM password_history WHERE username = ?", (user,))
conn.commit()
conn.close()
`;
  runSqliteScript(script, [username, password]);
}

export function setUserLockout(username: string = ADMIN_USERNAME, seconds: number = 300) {
  const until = Math.floor(Date.now() / 1000) + seconds;
  const script = `
import sqlite3, sys
db_path, user, lockout_until = sys.argv[1], sys.argv[2], int(sys.argv[3])
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("UPDATE users SET lockout_until = ?, failed_login_attempts = 5 WHERE username = ?", (lockout_until, user))
conn.commit()
conn.close()
`;
  runSqliteScript(script, [username, String(until)]);
}

export function clearUserLockout(username: string = ADMIN_USERNAME) {
  const script = `
import sqlite3, sys
db_path, user = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("UPDATE users SET lockout_until = NULL, failed_login_attempts = 0 WHERE username = ?", (user,))
conn.commit()
conn.close()
`;
  runSqliteScript(script, [username]);
}

export function seedLoginAttempts(ip: string, count: number) {
  const script = `
import sqlite3, sys, time
db_path, ip, count = sys.argv[1], sys.argv[2], int(sys.argv[3])
now = time.time()
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.executemany("INSERT INTO login_attempts (ip, timestamp) VALUES (?, ?)", [(ip, now) for _ in range(count)])
conn.commit()
conn.close()
`;
  runSqliteScript(script, [ip, String(count)]);
}

export function clearLoginAttempts(ip: string) {
  const script = `
import sqlite3, sys
db_path, ip = sys.argv[1], sys.argv[2]
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("DELETE FROM login_attempts WHERE ip = ?", (ip,))
conn.commit()
conn.close()
`;
  runSqliteScript(script, [ip]);
}

async function resetLoginAttemptsViaApi(ip?: string): Promise<boolean> {
  const endpoint = `${HUB_URL}/test/reset-login-attempts`;
  const payload = JSON.stringify(ip ? { ip, clear_ban: true } : { clear_ban: true });
  const headersBase = { 'Content-Type': 'application/json' };
  const timeoutMs = Number(process.env.E2E_API_AUX_TIMEOUT_MS || '8000');

  // Fast path: AGENT_TOKEN used by E2E setups.
  try {
    const res = await fetchWithTimeout(endpoint, {
      method: 'POST',
      headers: { ...headersBase, Authorization: `Bearer ${HUB_AGENT_TOKEN}` },
      body: payload
    }, timeoutMs);
    if (res.ok) return true;
    if (![401, 403].includes(res.status)) return false;
  } catch {}

  try {
    const res = await fetchWithTimeout(endpoint, {
      method: 'POST',
      headers: { ...headersBase, Authorization: 'Bearer hubsecret' },
      body: payload
    }, timeoutMs);
    if (res.ok) return true;
    if (![401, 403].includes(res.status)) return false;
  } catch {}

  // Fallback: admin user JWT.
  try {
    const apiLogin = await loginViaApi(ADMIN_USERNAME, ADMIN_PASSWORD);
    const adminToken = apiLogin?.accessToken;
    if (!adminToken) return false;
    const res = await fetchWithTimeout(endpoint, {
      method: 'POST',
      headers: { ...headersBase, Authorization: `Bearer ${adminToken}` },
      body: payload
    }, timeoutMs);
    return res.ok;
  } catch {
    return false;
  }
}

export async function ensureLoginAttemptsCleared(ip?: string) {
  // Prefer deterministic DB cleanup when local E2E DB is available.
  if (ip) {
    try {
      clearLoginAttempts(ip);
    } catch {}
  }

  // Existing-mode may not have local test DB access.
  // Prefer dedicated reset endpoint; fallback to waiting out short throttle window.
  if (!USE_EXISTING_SERVICES) return;
  if (await resetLoginAttemptsViaApi(ip)) return;

  const deadline = Date.now() + Number(process.env.E2E_AUTH_RATE_LIMIT_CLEAR_TIMEOUT_MS || '75000');
  while (Date.now() < deadline) {
    try {
      const res = await fetchWithTimeout(`${HUB_URL}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: ADMIN_USERNAME, password: ADMIN_PASSWORD })
      }, Number(process.env.E2E_API_LOGIN_TIMEOUT_MS || '15000'));
      if (res.status !== 429) {
        return;
      }
    } catch {}
    await sleep(1000);
  }

  throw new Error(`Could not clear auth rate limit for ${ip} within timeout`);
}
