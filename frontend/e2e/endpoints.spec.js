import { test, expect } from '@playwright/test';

// Stelle sicher, dass wir immer controller:8081 verwenden in Docker
const backendUrl = 'http://controller:8081';

// Debug-Ausgabe zur Diagnose
console.log('Verwendete Backend-URL:', backendUrl);
console.log('Umgebungsvariable PLAYWRIGHT_BASE_URL:', process.env.PLAYWRIGHT_BASE_URL);

test('Echtintegration: Frontend und Python-Backend', async ({ page, request }) => {
  // 1. Backend-Zustand vor dem Test abfragen
  const initialConfigResponse = await request.get(`${backendUrl}/config`);
  expect(initialConfigResponse.ok()).toBeTruthy();
  const initialConfig = await initialConfigResponse.json();
  // Annahme: Standardwert ist "lmstudio"
  expect(initialConfig.api_endpoints[0].type).toBe('lmstudio');

  // 2. Frontend öffnen und den Endpunkte-Bereich aufrufen
  await page.goto('/ui/');
  await page.waitForLoadState('networkidle');
  await Promise.all([
    page.waitForResponse(r => r.url().endsWith('/config') && r.ok()),
    page.click('text=Endpoints')
  ]);

  // Warte darauf, dass mindestens eine Zeile in der Tabelle gerendert wird
  await page.waitForSelector('tbody tr', { timeout: 30000 });
  const row = page.locator('tbody tr').first();
  
  // 3. Überprüfen, ob der initiale Endpunkt korrekt angezeigt wird
  await expect(row).toContainText('lmstudio');

  // 4. Bearbeitung des Endpunkts über das Frontend und Speichern
  await page.click('[data-test="edit"]');
  const inputs = row.locator('input');
  await inputs.first().fill('type2');
  await inputs.nth(1).fill('http://edited');
  await row.locator('[data-test="edit-models"]').selectOption(['m2']);
  await page.click('text=Save');

  // Überprüfen, ob im Frontend die Änderung übernommen wurde
  await expect(row).toContainText('type2');

  // 5. Überprüfen des aktualisierten Backend-Zustands
  const updatedResponse = await request.get(`${backendUrl}/config`);
  expect(updatedResponse.ok()).toBeTruthy();
  const updatedConfig = await updatedResponse.json();
  expect(updatedConfig.api_endpoints[0].type).toBe('type2');
  expect(updatedConfig.api_endpoints[0].url).toBe('http://edited');
  expect(updatedConfig.api_endpoints[0].models).toEqual(['m2']);

  // 6. Hinzufügen eines neuen Endpunkts über das Frontend
  await page.fill('[data-test="new-type"]', 'type3');
  await page.fill('[data-test="new-url"]', 'http://new');
  await page.selectOption('[data-test="new-models"]', ['m1']);
  await page.click('[data-test="add"]');
  const rows = page.locator('tbody tr');
  await expect(rows).toHaveCount(2);

  // Validiere Backend nach dem Hinzufügen
  const afterAddResponse = await request.get(`${backendUrl}/config`);
  expect(afterAddResponse.ok()).toBeTruthy();
  const afterAddConfig = await afterAddResponse.json();
  expect(afterAddConfig.api_endpoints).toHaveLength(2);
  const newEndpoint = afterAddConfig.api_endpoints.find(ep => ep.type === 'type3');
  expect(newEndpoint).toBeDefined();
  expect(newEndpoint.url).toBe('http://new');
  expect(newEndpoint.models).toEqual(['m1']);

  // 7. Löschen eines Endpunkts über das Frontend (angenommen, der erste wird gelöscht)
  await page.click('[data-test="delete"]');
  await expect(rows).toHaveCount(1);
  
  const afterDeleteResponse = await request.get(`${backendUrl}/config`);
  expect(afterDeleteResponse.ok()).toBeTruthy();
  const afterDeleteConfig = await afterDeleteResponse.json();
  expect(afterDeleteConfig.api_endpoints).toHaveLength(1);
  expect(afterDeleteConfig.api_endpoints[0].type).toBe('type3');
});

