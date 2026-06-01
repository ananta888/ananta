import { test, expect } from '@playwright/test';
import { loginFast } from './utils';

test.describe('Control Center Policy View', () => {
  test('shows denied actions and narrow approval payload', async ({ page, request }) => {
    await loginFast(page, request);
    await page.goto('/control-center/policies', { waitUntil: 'domcontentloaded' });

    await expect(page.getByText('Policies & Approvals')).toBeVisible();
    await expect(page.getByText('deny')).toBeVisible();

    await page.getByPlaceholder('z.B. tc-103').fill('tc-103');
    await page.getByPlaceholder('z.B. tool-77').fill('tool-77');
    await page.getByRole('button', { name: 'Narrow Approval senden' }).click();

    const payload = page.locator('pre.raw');
    await expect(payload).toContainText('"scope": "single_action"');
    await expect(payload).toContainText('"action_id": "tc-103"');
    await expect(payload).toContainText('"tool_call_id": "tool-77"');
  });
});
