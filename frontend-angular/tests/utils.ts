import { expect, Page, type APIRequestContext } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync, spawnSync } from 'node:child_process';

export const ADMIN_USERNAME = process.env.E2E_ADMIN_USER || 'admin';
export const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'AnantaAdminPassword123!';
export const HUB_URL = process.env.E2E_HUB_URL || 'http://localhost:5500';
export const ALPHA_URL = process.env.E2E_ALPHA_URL || 'http://localhost:5501';
export const BETA_URL = process.env.E2E_BETA_URL || 'http://localhost:5502';
export const TEST_LOGIN_IP = process.env.ANANTA_E2E_USE_EXISTING === '1' ? undefined : '127.0.0.1';
export const HUB_AGENT_TOKEN = process.env.E2E_HUB_AGENT_TOKEN || process.env.AGENT_TOKEN_HUB || 'generate_a_random_token_for_hub';
export const ALPHA_AGENT_TOKEN = process.env.E2E_ALPHA_AGENT_TOKEN || process.env.AGENT_TOKEN_ALPHA || 'generate_a_random_token_for_alpha';
export const BETA_AGENT_TOKEN = process.env.E2E_BETA_AGENT_TOKEN || process.env.AGENT_TOKEN_BETA || 'generate_a_random_token_for_beta';
const USE_EXISTING_SERVICES = process.env.ANANTA_E2E_USE_EXISTING === '1';
let hubHealthReady = false;
let hubHealthWarningLogged = false;

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
  for (let i = 0; i < attempts; i += 1) {
    try {
      const res = await fetch(`${HUB_URL}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password })
      });
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
  try { await resetUserAuthStateViaApi(username, password); } catch {}
  try { await ensureLoginAttemptsCleared(); } catch {}
}

async function provisionUserViaTestApi(
  username: string,
  password: string,
  role: 'admin' | 'user' = 'user'
): Promise<boolean> {
  try {
    const res = await fetch(`${HUB_URL}/test/provision-user`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password, role, overwrite: true })
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function deleteUserViaTestApi(username: string): Promise<boolean> {
  try {
    const res = await fetch(`${HUB_URL}/test/users/${encodeURIComponent(username)}`, {
      method: 'DELETE'
    });
    return res.ok || res.status === 404;
  } catch {
    return false;
  }
}

export async function resetUserAuthStateViaApi(username: string, password?: string): Promise<boolean> {
  try {
    const body: any = { username };
    if (password) body.password = password;
    const res = await fetch(`${HUB_URL}/test/reset-user-auth-state`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    return res.ok;
  } catch {
    return false;
  }
}

export async function prepareLoginPage(page: Page) {
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
  }, { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL, hubToken: HUB_AGENT_TOKEN, alphaToken: ALPHA_AGENT_TOKEN, betaToken: BETA_AGENT_TOKEN });
  await page.reload({ waitUntil: 'domcontentloaded' });
}

export async function login(page: Page, username = ADMIN_USERNAME, password = ADMIN_PASSWORD) {
  // Prevent cross-test bleed from IP-based login throttling.
  try { clearLoginAttempts('127.0.0.1'); } catch {}
  try { await ensureLoginAttemptsCleared(); } catch {}
  // Try to normalize admin auth state in shared compose-lite runs.
  await normalizeExistingAdminAuthState(username, password);
  await prepareLoginPage(page);
  const dashboard = page.getByRole('heading', { name: /System Dashboard/i });

  // Prefer API login to reduce UI bootstrap flakes on slow startup.
  const apiLogin = await loginViaApi(username, password);
  if (apiLogin?.accessToken) {
    await page.evaluate(({ hubUrl, alphaUrl, betaUrl, hubToken, alphaToken, betaToken, token, refreshToken }) => {
      localStorage.setItem('ananta.agents.v1', JSON.stringify([
        { name: 'hub', url: hubUrl, token: hubToken, role: 'hub' },
        { name: 'alpha', url: alphaUrl, token: alphaToken, role: 'worker' },
        { name: 'beta', url: betaUrl, token: betaToken, role: 'worker' }
      ]));
      localStorage.setItem('ananta.user.token', token);
      if (refreshToken) localStorage.setItem('ananta.user.refresh_token', refreshToken);
    }, { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL, hubToken: HUB_AGENT_TOKEN, alphaToken: ALPHA_AGENT_TOKEN, betaToken: BETA_AGENT_TOKEN, token: apiLogin.accessToken, refreshToken: apiLogin.refreshToken });
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(dashboard).toBeVisible({ timeout: 30000 });
    return;
  }
  if (apiLogin?.mfaRequired) {
    // In shared/local compose test mode, bypass interactive MFA by using static AGENT_TOKEN auth.
    await page.evaluate(({ hubUrl, alphaUrl, betaUrl, hubToken, alphaToken, betaToken }) => {
      localStorage.setItem('ananta.agents.v1', JSON.stringify([
        { name: 'hub', url: hubUrl, token: hubToken, role: 'hub' },
        { name: 'alpha', url: alphaUrl, token: alphaToken, role: 'worker' },
        { name: 'beta', url: betaUrl, token: betaToken, role: 'worker' }
      ]));
      localStorage.setItem('ananta.user.token', hubToken);
    }, { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL, hubToken: HUB_AGENT_TOKEN, alphaToken: ALPHA_AGENT_TOKEN, betaToken: BETA_AGENT_TOKEN });
    await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
    await expect(dashboard).toBeVisible({ timeout: 30000 });
    return;
  }

  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);

  const submit = page.locator('button.primary');
  const error = page.locator('.error-msg');

  const maxAttempts = Number(process.env.E2E_LOGIN_RETRY_ATTEMPTS || '4');
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      await expect(submit).toBeEnabled({ timeout: 5000 });
      await submit.click();
      await expect(dashboard).toBeVisible({ timeout: 30000 });
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
      await page.locator('input[name="password"]').fill(password);
    }
  }

  await expect(dashboard).toBeVisible({ timeout: 30000 });
}

export async function loginFast(
  page: Page,
  request: APIRequestContext,
  username = ADMIN_USERNAME,
  password = ADMIN_PASSWORD
) {
  await normalizeExistingAdminAuthState(username, password);
  await prepareLoginPage(page);

  const response = await request.post(`${HUB_URL}/login`, {
    data: {
      username,
      password,
    },
  });
  expect(response.ok()).toBeTruthy();
  const payload = await response.json() as any;
  const accessToken = payload?.data?.access_token;
  const refreshToken = payload?.data?.refresh_token;
  expect(accessToken).toBeTruthy();

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

  await page.goto('/dashboard', { waitUntil: 'domcontentloaded' });
  await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible({ timeout: 30000 });
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

export async function getAccessToken(username: string, password: string): Promise<string> {
  await normalizeExistingAdminAuthState(username, password);
  const apiLogin = await loginViaApi(username, password);
  if (apiLogin?.accessToken) return apiLogin.accessToken;
  if (apiLogin?.mfaRequired && USE_EXISTING_SERVICES && username === ADMIN_USERNAME) {
    return 'hubsecret';
  }

  const res = await postJson(`${HUB_URL}/login`, { username, password });
  if (!res.ok) {
    throw new Error(`Login failed for ${username}: ${res.status}`);
  }
  const payload = await res.json() as any;
  const token = payload?.data?.access_token;
  if (!token) {
    throw new Error(`No access token returned for ${username}`);
  }
  return token;
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

  // Fast path: AGENT_TOKEN used by E2E setups.
  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { ...headersBase, Authorization: `Bearer ${HUB_AGENT_TOKEN}` },
      body: payload
    });
    if (res.ok) return true;
    if (![401, 403].includes(res.status)) return false;
  } catch {}

  try {
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { ...headersBase, Authorization: 'Bearer hubsecret' },
      body: payload
    });
    if (res.ok) return true;
    if (![401, 403].includes(res.status)) return false;
  } catch {}

  // Fallback: admin user JWT.
  try {
    const apiLogin = await loginViaApi(ADMIN_USERNAME, ADMIN_PASSWORD);
    const adminToken = apiLogin?.accessToken;
    if (!adminToken) return false;
    const res = await fetch(endpoint, {
      method: 'POST',
      headers: { ...headersBase, Authorization: `Bearer ${adminToken}` },
      body: payload
    });
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
      const res = await fetch(`${HUB_URL}/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: ADMIN_USERNAME, password: ADMIN_PASSWORD })
      });
      if (res.status !== 429) {
        return;
      }
    } catch {}
    await sleep(1000);
  }

  throw new Error(`Could not clear auth rate limit for ${ip} within timeout`);
}
