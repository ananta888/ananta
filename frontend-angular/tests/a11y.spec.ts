import { test, expect } from '@playwright/test';
import { login } from './utils';
import AxeBuilder from '@axe-core/playwright';

test.describe('Accessibility Smoke Tests', () => {
  test('Login page should not have automatically detectable accessibility issues', async ({ page }) => {
    await page.goto('/login');
    
    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    
    expect(accessibilityScanResults.violations).toEqual([]);
  });

  test('Dashboard should not have automatically detectable accessibility issues', async ({ page }) => {
    await login(page);
    await expect(page.getByRole('heading', { name: /System Dashboard/i })).toBeVisible();
    await page.waitForLoadState('networkidle');
    
    const accessibilityScanResults = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
      .analyze();
    
    expect(accessibilityScanResults.violations).toEqual([]);
  });
});
