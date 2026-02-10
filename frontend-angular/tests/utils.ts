import { expect, Page } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';
import { execFileSync } from 'node:child_process';

export const ADMIN_USERNAME = process.env.E2E_ADMIN_USER || 'admin';
export const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD || 'admin';

export async function login(page: Page, username = ADMIN_USERNAME, password = ADMIN_PASSWORD) {
  for (let i = 0; i < 30; i += 1) {
    try {
      const res = await page.request.get('http://localhost:5000/health');
      if (res.ok()) break;
    } catch {}
    await page.waitForTimeout(500);
  }

  await page.goto('/login');
  await page.evaluate(() => {
    localStorage.clear();
    // Default Hub und Worker setzen, unverschlüsselt für Tests
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', url: 'http://localhost:5000', token: 'hubsecret', role: 'hub' },
      { name: 'alpha', url: 'http://localhost:5001', token: 'secret1', role: 'worker' },
      { name: 'beta', url: 'http://localhost:5002', token: 'secret2', role: 'worker' }
    ]));
  });
  await page.reload();
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
    } catch (e) {
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
