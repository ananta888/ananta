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
  try {
    await expect.poll(async () => {
      if (await dockContainer(page).isVisible().catch(() => false)) return true;
      return page.getByTestId('assistant-dock-launcher').isVisible().catch(() => false);
    }, { timeout: 10_000, intervals: [100, 250, 500] }).toBe(true);
    return true;
  } catch {
    return false;
  }
}

export async function ensureAssistantExpanded(page: Page): Promise<boolean> {
  // Route transitions briefly leave a hidden launcher from the previous
  // component instance in the DOM. Only interact with a visible control and
  // wait for the new dock instead of clicking that stale node.
  for (let i = 0; i < 40; i += 1) {
    if (await dockContainer(page).isVisible().catch(() => false)) break;
    const launcher = page.getByTestId('assistant-dock-launcher');
    if (await launcher.isVisible().catch(() => false)) {
      await launcher.click();
    }
    await page.waitForTimeout(250);
  }
  if (!await dockContainer(page).isVisible().catch(() => false)) return false;

  for (let i = 0; i < 8; i += 1) {
    const container = dockContainer(page);
    const visible = await container.first().isVisible().catch(() => false);
    if (!visible) {
      await page.waitForTimeout(500);
      continue;
    }

    const state = await container.getAttribute('data-state');
    if (state === 'minimized') {
      const openButton = container.getByRole('button', { name: 'Assistant oeffnen', exact: true });
      if (await openButton.isVisible().catch(() => false)) {
        await openButton.click();
      } else {
        await container.locator('[data-testid="assistant-dock-header"], .header').first().click();
      }
      await page.waitForTimeout(150);
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
      // Project-context URL synchronization can recreate the dock just after
      // it first becomes visible. Require the expanded state to survive one
      // render turn so callers never receive a stale positive result.
      await page.waitForTimeout(250);
      if (
        await input.first().isVisible().catch(() => false)
        && await container.getAttribute('data-state') === 'expanded'
      ) {
        return true;
      }
    }
    await container.locator('[data-testid="assistant-dock-header"], .header').first().click();
    await page.waitForTimeout(150);
  }

  if ((await assistantInput(page).count()) === 0) return false;
  return await assistantInput(page).first().isVisible().catch(() => false);
}
