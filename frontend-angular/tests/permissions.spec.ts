import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Permissions', () => {
  test('non-admin cannot manage templates, roles, or team types', async ({ page, request }) => {
    const username = 'e2e-user';
    const password = 'e2e-user-pw';

    await login(page);

    const token = await page.evaluate(() => localStorage.getItem('ananta.user.token'));
    const hubUrl = await page.evaluate(() => {
      const raw = localStorage.getItem('ananta.agents.v1');
      if (!raw) return 'http://localhost:5000';
      try {
        const agents = JSON.parse(raw);
        const hub = agents.find((a: any) => a.role === 'hub');
        return hub?.url || 'http://localhost:5000';
      } catch {
        return 'http://localhost:5000';
      }
    });

    if (token) {
      await request.delete(`${hubUrl}/users/${username}`, {
        headers: { Authorization: `Bearer ${token}` }
      }).catch(() => undefined);

      await request.post(`${hubUrl}/users`, {
        headers: { Authorization: `Bearer ${token}` },
        data: { username, password, role: 'user' }
      });
    }

    await page.getByRole('button', { name: /Logout/i }).click();
    await login(page, username, password);

    await page.goto('/templates');
    await expect(page.getByRole('button', { name: /Anlegen/i })).toBeDisabled();
    const editButtons = page.getByRole('button', { name: /Edit/i });
    if (await editButtons.count() > 0) {
      await expect(editButtons.first()).toBeDisabled();
    }

    await page.goto('/teams');
    await expect(page.getByRole('button', { name: /Typ Erstellen/i })).toBeDisabled();
    await expect(page.getByRole('button', { name: /Rolle Erstellen/i })).toBeDisabled();
  });
});
