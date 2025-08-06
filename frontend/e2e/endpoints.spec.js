import { test, expect } from '@playwright/test';

test('Echtintegration: Frontend und Python-Backend', async ({ page, request }) => {
  // 1. Backend-Zustand vor dem Test abfragen
  const initialConfigResponse = await request.get('/config');
  expect(initialConfigResponse.ok()).toBeTruthy();
  const initialConfig = await initialConfigResponse.json();
  // Annahme: initialer Endpunkt hat den Typ "type1"
  expect(initialConfig.api_endpoints[0].type).toBe('type1');

  // 2. Im Frontend den Endpunkte-Bereich aufrufen
  await page.goto('/');
  await page.click('text=Endpoints');

  // 3. Überprüfen, ob der initiale Endpunkt korrekt angezeigt wird
  const row = page.locator('tbody tr').first();
  await expect(row).toContainText('type1');

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
  const updatedResponse = await request.get('/config');
  expect(updatedResponse.ok()).toBeTruthy();
  const updatedConfig = await updatedResponse.json();
  // Es wird erwartet, dass der erste Endpunkt nun geupdated wurde
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
  const afterAddResponse = await request.get('/config');
  expect(afterAddResponse.ok()).toBeTruthy();
  const afterAddConfig = await afterAddResponse.json();
  expect(afterAddConfig.api_endpoints).toHaveLength(2);
  // Prüfe, dass der neue Endpunkt in der Liste enthalten ist
  const newEndpoint = afterAddConfig.api_endpoints.find(ep => ep.type === 'type3');
  expect(newEndpoint).toBeDefined();
  expect(newEndpoint.url).toBe('http://new');
  expect(newEndpoint.models).toEqual(['m1']);

  // 7. Löschen eines Endpunkts über das Frontend (angenommen, der erste wird gelöscht)
  await page.click('[data-test="delete"]');
  await expect(rows).toHaveCount(1);

  // Überprüfe den Zustand im Backend nach dem Löschen
  const afterDeleteResponse = await request.get('/config');
  expect(afterDeleteResponse.ok()).toBeTruthy();
  const afterDeleteConfig = await afterDeleteResponse.json();
  // Es wird erwartet, dass nur noch ein Endpunkt vorhanden ist
  expect(afterDeleteConfig.api_endpoints).toHaveLength(1);
  // Prüfe, dass der Endpunkt 'type3' vorhanden ist
  expect(afterDeleteConfig.api_endpoints[0].type).toBe('type3');
});