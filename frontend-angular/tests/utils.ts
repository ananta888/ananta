import { expect, Page } from '@playwright/test';

export async function login(page: Page, username = 'admin', password = 'admin') {
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

  const submit = page.getByRole('button', { name: 'Anmelden' });
  const dashboard = page.getByRole('heading', { name: /System Dashboard/i });
  const error = page.locator('.error-msg');

  for (let attempt = 0; attempt < 2; attempt += 1) {
    await submit.click();
    try {
      await expect(dashboard).toBeVisible({ timeout: 5000 });
      return;
    } catch {}
    if (await error.isVisible()) {
      await page.waitForTimeout(1000);
    }
  }

  await expect(dashboard).toBeVisible();
}
