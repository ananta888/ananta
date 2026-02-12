import { test, expect } from '@playwright/test';
import { login } from './utils';

test.describe('Stress & Load Tests', () => {
  test.beforeEach(async ({ page }) => {
    // Vor jedem Test einloggen
    await login(page);
  });

  test('Rapid navigation stress test', async ({ page }) => {
    const stressRoutes = [
      '/dashboard',
      '/agents',
      '/templates',
      '/teams',
      '/board',
      '/archived',
      '/audit-log',
      '/settings',
      '/graph'
    ];

    // Wir navigieren 3 Mal schnell durch alle Hauptseiten
    for (let i = 0; i < 3; i++) {
      for (const route of stressRoutes) {
        await page.goto(route);
        // Sicherstellen, dass die App noch läuft (kein totaler Crash)
        await expect(page.locator('app-root')).toBeVisible();
      }
    }
  });

  test('Concurrent data loading stress', async ({ page }) => {
    // Auf das Dashboard gehen, wo viele Daten geladen werden
    await page.goto('/dashboard');

    // Schnelle serielle Reloads erzeugen Last ohne Frame-Abbruch durch parallele Navigation.
    for (let i = 0; i < 5; i++) {
      await page.reload({ waitUntil: 'domcontentloaded' });
      await expect(page.locator('app-root')).toBeVisible();
    }

    await expect(page.locator('app-root')).toBeVisible();
  });

  test('Heavy component interaction', async ({ page }) => {
    // Zu den Agents navigieren
    await page.goto('/agents');
    
    // Simuliere schnelles Klicken zwischen Dashboard und Agents
    for (let i = 0; i < 5; i++) {
      await page.goto('/dashboard');
      await page.goto('/agents');
    }
    await expect(page.locator('app-root')).toBeVisible();
  });
});
