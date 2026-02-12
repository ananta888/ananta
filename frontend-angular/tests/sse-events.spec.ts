import { test, expect } from '@playwright/test';
import { HUB_URL, login } from './utils';

test.describe('SSE Events', () => {
  test('updates agent token from system events stream', async ({ page }) => {
    await page.route(`${HUB_URL}/api/system/events*`, async route => {
      await route.fulfill({
        status: 200,
        contentType: 'text/event-stream',
        body: 'data: {"type":"token_rotated","data":{"new_token":"rotated-token-123"}}\n\n'
      });
    });

    await login(page);
    await page.goto('/agents');

    const hubCard = page.locator('.card').filter({ hasText: 'hub' }).first();
    await hubCard.locator('summary').click();

    const tokenInput = hubCard.getByLabel('Token');
    await expect(tokenInput).toHaveValue('rotated-token-123');
  });
});
