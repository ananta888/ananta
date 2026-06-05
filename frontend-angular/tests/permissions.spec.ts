import { test, expect } from '@playwright/test';
import { login, waitForHeaderRole } from './utils';

test.describe('Permissions', () => {
  test('non-admin cannot manage templates, roles, or team types', async ({ page }) => {
    await login(page);

    await page.evaluate(() => {
      const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
      const payload = btoa(JSON.stringify({ sub: 'e2e-user', role: 'user' }));
      localStorage.setItem('ananta.user.token', `${header}.${payload}.sig`);
    });
    await page.reload();
    await waitForHeaderRole(page, 'user');

    await page.goto('/templates');
    await expect(page.getByRole('button', { name: /Anlegen/i })).toBeDisabled();
    const editButtons = page.getByRole('button', { name: /Edit/i });
    if (await editButtons.count() > 0) {
      await expect(editButtons.first()).toBeDisabled();
    }

    await page.goto('/teams');
    await expect(page.getByRole('button', { name: /Admin-\/Studio-Modus/i })).toHaveCount(0);
    await expect(page.getByRole('button', { name: /^Advanced$/i })).toHaveCount(0);
    await expect(page.locator('.teams-editor-panel')).toHaveCount(0);
  });
});
