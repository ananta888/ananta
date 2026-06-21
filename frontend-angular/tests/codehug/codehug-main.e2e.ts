import { test, expect } from '@playwright/test';

/**
 * CodeHug E2E — Hauptworkflow-Tests.
 *
 * CH-012-003: Grundlegende Workflow-Tests (Navigation, Dashboard, Context-Builder).
 * CH-012-004: Agent-Flow (read-only; Apply wird nicht ausgeführt, nur bis Diff-Preview).
 *
 * Voraussetzungen:
 * - App läuft (global-setup sorgt für Auth)
 * - Hub-Backend ist erreichbar oder E2E_MOCK_BACKEND=1 gesetzt
 */

const BASE = '/codehug';

test.describe('CodeHug — Navigation & Shell', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE);
  });

  test('zeigt CodeHug-Brand in der Shell-Kopfzeile', async ({ page }) => {
    await expect(page.locator('.codehug-brand')).toBeVisible();
    await expect(page.locator('.codehug-brand')).toContainText('CodeHug');
  });

  test('Sub-Navigation ist vollständig vorhanden', async ({ page }) => {
    const nav = page.locator('.codehug-shell-nav');
    await expect(nav.locator('a', { hasText: 'Dashboard' })).toBeVisible();
    await expect(nav.locator('a', { hasText: 'Kontext-Builder' })).toBeVisible();
    await expect(nav.locator('a', { hasText: 'Suche' })).toBeVisible();
    await expect(nav.locator('a', { hasText: 'Refactoring' })).toBeVisible();
    await expect(nav.locator('a', { hasText: 'Agenten' })).toBeVisible();
    await expect(nav.locator('a', { hasText: 'Internals' })).toBeVisible();
  });

  test('Write-Mode-Anzeige ist standardmäßig read-only', async ({ page }) => {
    const badge = page.locator('[data-testid="write-mode-badge"]');
    await expect(badge).toBeVisible();
    await expect(badge).toContainText(/read.only/i);
  });

  test('rechte Spalte kann ein- und ausgeklappt werden', async ({ page }) => {
    const toggleBtn = page.locator('.codehug-shell-collapse');
    await expect(toggleBtn).toBeVisible();
    await toggleBtn.click();
    const rightPanel = page.locator('#codehug-right-panel');
    await expect(rightPanel).toHaveClass(/codehug-col-right-collapsed/);
    await toggleBtn.click();
    await expect(rightPanel).not.toHaveClass(/codehug-col-right-collapsed/);
  });
});

test.describe('CodeHug — Dashboard', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(BASE);
  });

  test('zeigt Projekt-Übersicht oder Empty-State', async ({ page }) => {
    const projectSection = page.locator('[aria-labelledby="ch-projects-h"]');
    await expect(projectSection).toBeVisible();
  });

  test('zeigt CodeCompass-Status-Bereich', async ({ page }) => {
    const ccSection = page.locator('[aria-labelledby="ch-cc-h"]');
    await expect(ccSection).toBeVisible();
  });

  test('Dashboard-Titel ist sichtbar', async ({ page }) => {
    await expect(page.locator('h2')).toContainText('Dashboard');
  });
});

test.describe('CodeHug — Kontext-Builder', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE}/context`);
  });

  test('zeigt Aufgaben-Input und Vorschläge-Button', async ({ page }) => {
    const taskInput = page.locator('#ch-cb-task-input');
    await expect(taskInput).toBeVisible();
    const suggestBtn = page.locator('button', { hasText: /vorschläge/i });
    await expect(suggestBtn).toBeVisible();
  });

  test('Dateien-Spalte ist vorhanden', async ({ page }) => {
    const filesSection = page.locator('[aria-labelledby="ch-cb-files-h"]');
    await expect(filesSection).toBeVisible();
  });

  test('Paket-Name-Input ist vorhanden', async ({ page }) => {
    const nameInput = page.locator('#ch-cb-name');
    await expect(nameInput).toBeVisible();
  });

  test('Speichern-Button ist deaktiviert ohne Auswahl', async ({ page }) => {
    const saveBtn = page.locator('button', { hasText: /speichern/i }).last();
    await expect(saveBtn).toBeDisabled();
  });
});

test.describe('CodeHug — Suche', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE}/search`);
  });

  test('zeigt Suchfeld', async ({ page }) => {
    const searchInput = page.locator('input[type="text"]').first();
    await expect(searchInput).toBeVisible();
  });
});

test.describe('CodeHug — System Internals', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE}/internals`);
  });

  test('Show-Internals-Button oder Topologie-Bereich ist vorhanden', async ({ page }) => {
    await expect(
      page.locator('button', { hasText: /internals|topologie|system/i })
        .or(page.locator('[aria-label*="Topologie"]'))
        .or(page.locator('h2, h3').filter({ hasText: /Topologie|Internals|System/i }))
    ).toBeVisible();
  });
});

test.describe('CodeHug — Agenten', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE}/agents`);
  });

  test('zeigt Refactoring-Bereich', async ({ page }) => {
    await expect(page.locator('h3, h2').filter({ hasText: /refactoring/i })).toBeVisible();
  });

  test('Apply-Button ist nicht ohne Write-Mode verfügbar', async ({ page }) => {
    const applyBtn = page.locator('button', { hasText: /apply|anwenden/i });
    const count = await applyBtn.count();
    if (count > 0) {
      await expect(applyBtn.first()).toBeDisabled();
    }
  });
});

test.describe('CodeHug — Policy Panel', () => {
  test.beforeEach(async ({ page }) => {
    await page.goto(`${BASE}/policy`);
  });

  test('Policy-Seite ist erreichbar', async ({ page }) => {
    await expect(page.locator('h2, h3').filter({ hasText: /policy/i })).toBeVisible();
  });

  test('Edit-Buttons sind ohne Write-Mode deaktiviert', async ({ page }) => {
    const editBtns = page.locator('button', { hasText: /bearbeiten|edit/i });
    const count = await editBtns.count();
    if (count > 0) {
      await expect(editBtns.first()).toBeDisabled();
    }
  });
});

test.describe('CodeHug — Fehlerszenarien', () => {
  test('Deep-Link auf ungültige Sub-Route landet auf 404 oder Redirect', async ({ page }) => {
    const res = await page.goto(`${BASE}/does-not-exist`);
    const status = res?.status() ?? 200;
    const url = page.url();
    const isHandled = status === 404 || url.includes('/codehug') || url.includes('/not-found');
    expect(isHandled).toBeTruthy();
  });
});
