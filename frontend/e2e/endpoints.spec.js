import { test, expect } from '@playwright/test';

// Ziel dieses Tests: Nur neue Endpunkte hinzufügen und diese am Ende wieder entfernen.
// Bestehende Endpunkte dürfen nicht verändert werden. Der Test räumt sich selbst auf.

test('Echtintegration: Endpunkte nur hinzufügen und entfernen (keine Änderungen am Bestand)', async ({ page, request }) => {
  // 1) Backend-Zustand vor dem Test abfragen und sichern
  const initialConfigResponse = await request.get(`/config`);
  expect(initialConfigResponse.ok()).toBeTruthy();
  const initialConfig = await initialConfigResponse.json();
  const initialEndpoints = Array.isArray(initialConfig.api_endpoints) ? [...initialConfig.api_endpoints] : [];

  // 2) Frontend öffnen und den Endpunkte-Bereich aufrufen
  await page.goto('/ui/');
  await page.waitForLoadState('networkidle');
  await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/config') && r.ok()),
    page.click('text=Endpoints')
  ]);

  // Warte darauf, dass mindestens eine Zeile in der Tabelle gerendert wird
  await page.waitForSelector('tbody tr', { timeout: 30000 });

  // 3) Neuen Endpunkt mit eindeutigem Test-Prefix hinzufügen
  const uid = `e2e-${Date.now()}`;
  const newType = `lmstudio-${uid}`;
  const newUrl = `http://new-${uid}`;

  await page.fill('[data-test="new-type"]', newType);
  await page.fill('[data-test="new-url"]', newUrl);
  // Nutzt Test-Modelle (m1/m2), die in der Test-Umgebung aktiviert werden
  await page.selectOption('[data-test="new-models"]', ['m1']);
  await page.click('[data-test="add"]');

  const rows = page.locator('tbody tr');
  await expect(rows).toHaveCount(initialEndpoints.length + 1);

  // 4) Backend validieren: Neuer Endpunkt vorhanden
  const afterAddResponse = await request.get(`/config`);
  expect(afterAddResponse.ok()).toBeTruthy();
  const afterAddConfig = await afterAddResponse.json();
  expect(afterAddConfig.api_endpoints).toHaveLength(initialEndpoints.length + 1);
  const created = afterAddConfig.api_endpoints.find(ep => ep.type === newType && ep.url === newUrl);
  expect(created).toBeDefined();
  expect(created.models).toEqual(['m1']);

  // 5) Nur den neu erstellten Endpunkt wieder löschen (Cleanup)
  const newRow = page.locator('tbody tr', { hasText: newType });
  await expect(newRow).toBeVisible();
  await newRow.locator('[data-test="delete"]').click();

  // UI sollte wieder zur Ausgangsanzahl zurückkehren
  await expect(rows).toHaveCount(initialEndpoints.length);

  // 6) Backend validieren: wieder Ausgangszustand
  const afterDeleteResponse = await request.get(`/config`);
  expect(afterDeleteResponse.ok()).toBeTruthy();
  const afterDeleteConfig = await afterDeleteResponse.json();
  expect(afterDeleteConfig.api_endpoints).toHaveLength(initialEndpoints.length);
  // Sicherstellen, dass der neu erstellte Eintrag nicht mehr existiert
  expect(afterDeleteConfig.api_endpoints.find(ep => ep.type === newType && ep.url === newUrl)).toBeFalsy();
});

