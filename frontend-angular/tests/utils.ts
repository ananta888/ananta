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
      { name: 'hub', url: 'http://127.0.0.1:5000', token: 'hubsecret', role: 'hub' },
      { name: 'alpha', url: 'http://127.0.0.1:5001', token: 'secret1', role: 'worker' },
      { name: 'beta', url: 'http://127.0.0.1:5002', token: 'secret2', role: 'worker' }
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
