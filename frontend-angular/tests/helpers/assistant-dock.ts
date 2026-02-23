import { expect, Page } from '@playwright/test';

function dockContainer(page: Page) {
  return page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
}

export async function hasAssistantDock(page: Page): Promise<boolean> {
  if ((await dockContainer(page).count()) > 0) return true;
  return (await page.getByText(/AI Assistant/i).first().count()) > 0;
}

export async function ensureAssistantExpanded(page: Page): Promise<boolean> {
  let container = dockContainer(page);
  if (await container.count() === 0) {
    const opener = page.getByText(/AI Assistant/i).first();
    if (await opener.count()) {
      await opener.click();
    }
    container = dockContainer(page);
  }
  if (await container.count() === 0) return false;

  await expect(container).toBeVisible({ timeout: 15000 });
  const state = await container.getAttribute('data-state');
  if (state === 'minimized') {
    await page
      .locator('[data-testid="assistant-dock-header"], .ai-assistant-container .header, .ai-assistant-container button')
      .first()
      .click();
  }
  await expect(
    page
      .locator('[data-testid="assistant-dock-input"], input[placeholder="Ask me anything..."], input[placeholder*="Frage mich"]')
      .first()
  ).toBeVisible({ timeout: 15000 });
  return true;
}
