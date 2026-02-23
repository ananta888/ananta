import { Page, Route } from '@playwright/test';

type JsonBody = Record<string, unknown> | Array<unknown>;

export async function mockJson(page: Page, urlPattern: string, body: JsonBody, status = 200) {
  await page.route(urlPattern, async (route: Route) => {
    await route.fulfill({
      status,
      contentType: 'application/json',
      body: JSON.stringify(body),
    });
  });
}

