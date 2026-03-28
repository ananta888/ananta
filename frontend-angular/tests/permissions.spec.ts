import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Permissions', () => {
  test('non-admin cannot manage templates, roles, or team types', async ({ page }) => {
    await login(page);

    await page.evaluate(() => {
      const header = btoa(JSON.stringify({ alg: 'none', typ: 'JWT' }));
      const payload = btoa(JSON.stringify({ sub: 'e2e-user', role: 'user' }));
      localStorage.setItem('ananta.user.token', `${header}.${payload}.sig`);
    });
    await page.reload();

    await page.goto('/templates');
    await expect(page.getByRole('button', { name: /Anlegen/i })).toBeDisabled();
    const editButtons = page.getByRole('button', { name: /Edit/i });
    if (await editButtons.count() > 0) {
      await expect(editButtons.first()).toBeDisabled();
    }

    await page.goto('/teams');
    await page.getByRole('button', { name: /^Advanced$/i }).click();
    await page.getByRole('button', { name: /^Team-Typen$/i }).click();
    await expect(page.getByRole('button', { name: /Typ Erstellen/i })).toBeDisabled();
    await page.getByRole('button', { name: /^Rollen$/i }).click();
    await expect(page.getByRole('button', { name: /Rolle Erstellen/i })).toBeDisabled();
  });
});
