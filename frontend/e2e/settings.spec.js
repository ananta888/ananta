import { test, expect } from '@playwright/test';

// Log der Test-Umgebungsvariablen für Debugging
test.beforeAll(() => {
  console.log('PLAYWRIGHT_BASE_URL:', process.env.PLAYWRIGHT_BASE_URL);
  console.log('Test wird ausgeführt mit URL:', process.env.PLAYWRIGHT_BASE_URL || 'http://controller:8081');
});

test('change active agent persists via API', async ({ page }) => {
  const config = {
    active_agent: 'Architect',
    agents: { Architect: {}, Developer: {} }
  };

  await page.route('**/config', route => {
    if (route.request().method() === 'GET') {
      route.fulfill({
        contentType: 'application/json',
        body: JSON.stringify(config)
      });
    } else {
      route.continue();
    }
  });

  let savedBody = null;
  await page.route('**/config/active_agent', async route => {
    savedBody = await route.request().postDataJSON();
    route.fulfill({ status: 200, body: '{}' });
  });

  await page.goto('/ui/');
  await page.waitForLoadState('networkidle');
  await page.click('text=Einstellungen');
  const select = page.locator('select');
  await expect(select).toHaveValue('Architect');

  await select.selectOption('Developer');
  await page.click('[data-test="save"]');

  expect(savedBody).toEqual({ active_agent: 'Developer' });
});
