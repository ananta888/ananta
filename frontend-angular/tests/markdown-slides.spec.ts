import { test, expect } from '@playwright/test';
import { assertNoUnhandledBrowserErrors, loginFast } from './utils';

test.describe('Markdown slides', () => {
  test('loads sample, navigates, edits, presents, and sanitizes unsafe content', async ({ page, request }) => {
    await loginFast(page, request);

    await page.goto('/markdown-slides', { waitUntil: 'domcontentloaded' });
    await expect(page.getByTestId('markdown-slides-root')).toBeVisible();
    await page.getByTestId('markdown-load-sample').click();
    await expect(page.getByTestId('markdown-slide-list')).toContainText('Hub Worker Flow');
    await expect(page.getByTestId('markdown-slide-preview')).toContainText('Ananta Markdown Slides');

    await page.getByTestId('markdown-next-slide').click();
    await expect(page.getByTestId('markdown-selected-title')).toContainText('Hub Worker Flow');

    await page.getByTestId('markdown-slide-editor').fill('# Safe Title\n\n<script>window.__markdownSlideExecuted = true</script>\n\n[bad](javascript:alert(1))');
    await expect(page.getByTestId('markdown-slide-preview')).toContainText('Safe Title');
    await expect(page.locator('script', { hasText: 'markdownSlideExecuted' })).toHaveCount(0);
    await expect(page.getByTestId('markdown-diagnostics')).toContainText('Script tags are not allowed');
    await expect(page.getByTestId('markdown-diagnostics')).toContainText('javascript: URLs are not allowed');
    await expect(page.evaluate(() => (window as any).__markdownSlideExecuted)).resolves.toBeFalsy();

    await page.getByTestId('markdown-presentation-toggle').click();
    await expect(page.getByTestId('markdown-presentation-stage')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(page.getByTestId('markdown-presentation-stage')).toHaveCount(0);
    await assertNoUnhandledBrowserErrors(page);
  });
});
