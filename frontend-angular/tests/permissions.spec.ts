import { test, expect } from '@playwright/test';
import {
  createUserAsAdmin,
  deleteUserAsAdmin,
  login,
  waitForHeaderRole,
} from './utils';

test.describe('Permissions', () => {
  test('non-admin cannot manage templates, roles, or team types', async ({ page }) => {
    const username = `e2e-permissions-${Date.now()}`;
    const password = 'Test-User-Password-123!';
    await createUserAsAdmin(username, password, 'user');

    try {
      await login(page, username, password);
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
    } finally {
      await deleteUserAsAdmin(username);
    }
  });
});
