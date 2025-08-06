import { test, expect } from '@playwright/test';

test('manage endpoints persists via API', async ({ page }) => {
  const config = {
    api_endpoints: [{ type: 'type1', url: 'http://old', models: ['m1'] }],
    models: ['m1', 'm2']
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

  const bodies = [];
  await page.route('**/config/api_endpoints', async route => {
    bodies.push(await route.request().postDataJSON());
    route.fulfill({ status: 200, body: '{}' });
  });

  await page.goto('/');
  await page.click('text=Endpoints');

  // start editing and cancel
  await page.click('[data-test="edit"]');
  const row = page.locator('tbody tr').first();
  const inputs = row.locator('input');
  await inputs.first().fill('type2');
  await inputs.nth(1).fill('http://edited');
  await row.locator('[data-test="edit-models"]').selectOption(['m2']);
  await page.click('text=Cancel');
  await expect(row).toContainText('type1');
  expect(bodies).toHaveLength(0);

  // edit and save
  await page.click('[data-test="edit"]');
  await inputs.first().fill('type2');
  await inputs.nth(1).fill('http://edited');
  await row.locator('[data-test="edit-models"]').selectOption(['m2']);
  await page.click('text=Save');
  await expect(row).toContainText('type2');
  expect(bodies[0]).toEqual({
    api_endpoints: [
      { type: 'type2', url: 'http://edited', models: ['m2'] }
    ]
  });

  // add endpoint
  await page.fill('[data-test="new-type"]', 'type3');
  await page.fill('[data-test="new-url"]', 'http://new');
  await page.selectOption('[data-test="new-models"]', ['m1']);
  await page.click('[data-test="add"]');
  await expect(page.locator('tbody tr')).toHaveCount(2);
  expect(bodies[1]).toEqual({
    api_endpoints: [
      { type: 'type2', url: 'http://edited', models: ['m2'] },
      { type: 'type3', url: 'http://new', models: ['m1'] }
    ]
  });

  // delete endpoint
  await page.click('[data-test="delete"]');
  await expect(page.locator('tbody tr')).toHaveCount(1);
  expect(bodies[2]).toEqual({
    api_endpoints: [
      { type: 'type3', url: 'http://new', models: ['m1'] }
    ]
  });
});
