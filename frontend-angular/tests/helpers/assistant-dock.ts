import { expect, Page } from '@playwright/test';

function dockContainer(page: Page) {
  return page.locator('[data-testid="assistant-dock"], .ai-assistant-container').first();
}

export function assistantInput(page: Page) {
  return page
    .locator(
      '[data-testid="assistant-dock-input"], input[placeholder*="Ask me anything"], input[placeholder*="Frage mich etwas"], input[placeholder*="Frage mich"]'
    )
    .first();
}

export async function hasAssistantDock(page: Page): Promise<boolean> {
  if ((await dockContainer(page).count()) > 0) return true;
  return (await page.getByText(/AI Assistant/i).first().count()) > 0;
}

export async function ensureAssistantExpanded(page: Page): Promise<boolean> {
  if (await dockContainer(page).count() === 0) {
    const opener = page.getByText(/AI Assistant/i).first();
    if (await opener.count()) {
      await opener.click();
    }
  }
  if (await dockContainer(page).count() === 0) return false;

  for (let i = 0; i < 5; i += 1) {
    const container = dockContainer(page);
    const visible = await container.first().isVisible().catch(() => false);
    if (!visible) {
      await page.waitForTimeout(500);
      continue;
    }

    const state = await container.getAttribute('data-state');
    if (state === 'minimized') {
      await page
        .locator('[data-testid="assistant-dock-header"], .ai-assistant-container .header, .ai-assistant-container button')
        .first()
        .click();
      continue;
    }

    // Ensure overlay panels are closed so the assistant controls are visible.
    const overlayToggles = page.locator('.mini-footer-btn.share-btn.active, .mini-footer-btn.snake-chat-btn.active, .mini-footer-btn.config-btn.active');
    const overlayCount = await overlayToggles.count();
    for (let idx = 0; idx < overlayCount; idx += 1) {
      await overlayToggles.nth(idx).click().catch(() => {});
    }

    const input = container.locator('[data-testid="assistant-dock-input"], input[placeholder*="Ask me anything"], input[placeholder*="Frage mich"]');
    if ((await input.count()) > 0 && (await input.first().isVisible().catch(() => false))) {
      return true;
    }
    await page
      .locator('[data-testid="assistant-dock-header"], .ai-assistant-container .header, .ai-assistant-container button')
      .first()
      .click();
  }

  if ((await assistantInput(page).count()) === 0) return false;
  return await assistantInput(page).first().isVisible().catch(() => false);
}
