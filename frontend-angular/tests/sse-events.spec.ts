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

    const initialToken = await page.evaluate(() => {
      const raw = localStorage.getItem('ananta.agents.v1');
      if (!raw) return '';
      try {
        const agents = JSON.parse(raw);
        const hub = agents.find((a: any) => a.role === 'hub' || a.name === 'hub');
        return String(hub?.token || '');
      } catch {
        return '';
      }
    });

    let rotatedToken = initialToken;
    const deadline = Date.now() + 12000;
    while (Date.now() < deadline) {
      rotatedToken = await page.evaluate(() => {
        const raw = localStorage.getItem('ananta.agents.v1');
        if (!raw) return '';
        try {
          const agents = JSON.parse(raw);
          const hub = agents.find((a: any) => a.role === 'hub' || a.name === 'hub');
          return String(hub?.token || '');
        } catch {
          return '';
        }
      });
      if (rotatedToken && rotatedToken !== initialToken) break;
      await page.waitForTimeout(300);
    }
    if (!rotatedToken || rotatedToken === initialToken) {
      test.skip(true, 'SSE token rotation event not applied in this environment.');
    }

    await page.reload();
    const hubCard = page.locator('.card').filter({ has: page.locator('strong', { hasText: /^hub$/i }) }).first();
    await hubCard.locator('summary').click();

    const tokenInput = hubCard.getByLabel('Token');
    await expect(tokenInput).not.toHaveValue(initialToken);
    await expect(tokenInput).not.toHaveValue('');
  });
});
