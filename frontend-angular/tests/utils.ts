import { expect, Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

export const ADMIN_USERNAME = process.env.E2E_ADMIN_USER || 'admin';
export const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin';
export const HUB_URL = process.env.E2E_HUB_URL || 'http://localhost:5500';
export const ALPHA_URL = process.env.E2E_ALPHA_URL || 'http://localhost:5501';
export const BETA_URL = process.env.E2E_BETA_URL || 'http://localhost:5502';
let hubHealthReady = false;
let hubHealthWarningLogged = false;

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitForHub(): Promise<boolean> {
  if (hubHealthReady) return true;
  for (let i = 0; i < 2; i += 1) {
    let timeoutId: NodeJS.Timeout | undefined;
    try {
      const controller = new AbortController();
      timeoutId = setTimeout(() => controller.abort(), 400);
      const res = await fetch(`${HUB_URL}/health`, { signal: controller.signal });
      if (res.ok) {
        hubHealthReady = true;
        return true;
      }
    } catch {}
    finally {
      if (timeoutId) clearTimeout(timeoutId);
    }
    await sleep(100);
  }
  return false;
}

export async function prepareLoginPage(page: Page) {
  const hubReady = await waitForHub();
  if (!hubReady && !hubHealthWarningLogged) {
    hubHealthWarningLogged = true;
    console.warn(`Hub healthcheck still failing for ${HUB_URL}; continuing with login page setup.`);
  }
  await page.goto('/login');
  await page.evaluate(({ hubUrl, alphaUrl, betaUrl }) => {
    localStorage.clear();
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', url: hubUrl, token: 'hubsecret', role: 'hub' },
      { name: 'alpha', url: alphaUrl, token: 'secret1', role: 'worker' },
      { name: 'beta', url: betaUrl, token: 'secret2', role: 'worker' }
    ]));
  }, { hubUrl: HUB_URL, alphaUrl: ALPHA_URL, betaUrl: BETA_URL });
  await page.reload();
}

export async function login(page: Page, username = ADMIN_USERNAME, password = ADMIN_PASSWORD) {
  // Prevent cross-test bleed from IP-based login throttling.
  try { clearLoginAttempts('127.0.0.1'); } catch {}
  await prepareLoginPage(page);
  await page.locator('input[name="username"]').fill(username);
  await page.locator('input[name="password"]').fill(password);

  const submit = page.locator('button.primary');
  const dashboard = page.getByRole('heading', { name: /System Dashboard/i });
  const error = page.locator('.error-msg');

  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      await expect(submit).toBeEnabled({ timeout: 5000 });
      await submit.click();
      await expect(dashboard).toBeVisible({ timeout: 5000 });
      return;
    } catch (e: any) {
      console.warn(`Login attempt ${attempt + 1} failed: ${e.message}`);
      if (await error.isVisible()) {
        console.warn(`Error message visible: ${await error.innerText()}`);
      }
      await page.reload();
      await page.locator('input[name="username"]').fill(username);
      await page.locator('input[name="password"]').fill(password);
    }
  }

  await expect(dashboard).toBeVisible();
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
  const adminToken = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);
  const res = await postJson(`${HUB_URL}/users`, { username, password, role }, adminToken);
  if (!res.ok) {
    throw new Error(`Create user failed (${username}): ${res.status}`);
  }
}

export async function deleteUserAsAdmin(username: string) {
  const adminToken = await getAccessToken(ADMIN_USERNAME, ADMIN_PASSWORD);
  const res = await fetch(`${HUB_URL}/users/${encodeURIComponent(username)}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${adminToken}` }
  });
  if (!res.ok && res.status !== 404) {
    throw new Error(`Delete user failed (${username}): ${res.status}`);
  }
}

function getTestDbPath() {
  return path.resolve(__dirname, '..', '..', 'data_test_e2e', 'hub', 'ananta.db');
}

function runSqliteScript(script: string, args: string[]) {
  const dbPath = getTestDbPath();
  if (!fs.existsSync(dbPath)) {
    throw new Error(`E2E database not found: ${dbPath}`);
  }
  execFileSync('python', ['-c', script, dbPath, ...args], { stdio: 'ignore' });
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
