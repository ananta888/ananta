import AxeBuilder from '@axe-core/playwright';
import { expect, Page, Route, test } from '@playwright/test';

async function json(route: Route, data: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(status >= 400 ? data : { status: 'success', data }),
  });
}

function descriptor(index: number) {
  const fixtures = [
    ['codex', 'gpt-codex', 'cli:codex', 'Codex CLI', 'cloud', 'configured'],
    ['claude_code', 'claude-default', 'cli:claude_code', 'Claude Code', 'cloud', 'configured'],
    ['openrouter', 'openrouter-model', 'api:openrouter', 'OpenRouter Model', 'cloud', 'discovered'],
    ['ollama', 'ollama-model', 'api:ollama', 'Ollama Model', 'local', 'discovered'],
    ['lmstudio', 'lmstudio-model', 'api:lmstudio', 'LM Studio Model', 'local', 'discovered'],
  ] as const;
  const selected = fixtures[index] ?? [
    'lmstudio', `synthetic-${index}`, 'api:lmstudio', `Synthetic Model ${index}`, 'local', 'discovered',
  ] as const;
  return {
    schema: 'ananta.model-inventory-descriptor.v2',
    provider_id: selected[0], model_id: selected[1], executor_id: selected[2],
    display_name: selected[3], runtime: selected[4], source_ids: [`source:${selected[0]}`],
    source_kinds: [selected[5]], profile_ids: [`profile-${index}`], aliases: [],
    availability: 'available', health: 'healthy', configured: true,
    installed: selected[4] === 'local', loaded: selected[4] === 'local', listing_supported: selected[5] === 'discovered',
    auth_mode: null, auth_ready: null, context_window: 32768, quantization: index === 4 ? 'Q8_0' : null,
    input_modalities: ['text'], output_modalities: ['text'],
    price_input_per_million: null, price_output_per_million: null,
    capabilities: [{ capability_id: 'chat', value: 'supported', evidence: 'declared', source_id: `source:${selected[0]}` }],
    metadata_facts: [], conflicts: [], used_by_consumers: [],
  };
}

async function modelSettingsFixtures(page: Page) {
  const token = `e30.${Buffer.from(JSON.stringify({
    sub: 'model-admin', role: 'admin', exp: 4_102_444_800,
    capabilities: [
      'model_catalog.refresh', 'model_catalog.set_default', 'model_routing.read',
      'model_routing.validate', 'model_routing.export', 'model_routing.mutate',
    ],
  })).toString('base64url')}.sig`;
  await page.addInitScript(({ token }) => {
    localStorage.setItem('ananta.user.token', token);
    localStorage.setItem('ananta.shell.mode', 'advanced');
    localStorage.setItem('ananta.agents.v1', JSON.stringify([
      { name: 'hub', role: 'hub', url: 'http://127.0.0.1:5000', token: '' },
    ]));
  }, { token });

  const models = Array.from({ length: 5000 }, (_, index) => descriptor(index));
  const consumers = Array.from({ length: 500 }, (_, index) => ({
    schema: 'ananta.model-consumer.v1', consumer_id: `task.consumer-${index}`,
    label: `Consumer ${index}`, category: 'performance', required_capabilities: [],
    allowed_scopes: ['global', 'agent'], routable: true, default_model_role: 'any',
    legacy_config_paths: [], mutation_capability: 'model_routing.mutate',
    registration_source: 'builtin', non_routable_reason: null,
  }));
  let routing = { schema: 'ananta.model-routing-config.v1', revision: 0, assignments: [], fallback_groups: [] } as any;
  let saveCalls = 0;
  let migrationCalls = 0;
  let conflictNext = false;

  await page.route('**://127.0.0.1:5000/**', route => json(route, {}));
  await page.route('**/me', route => json(route, { sub: 'model-admin', role: 'admin' }));
  await page.route('**/api/projects', route => json(route, { items: [], count: 0 }));
  await page.route('**/config/features/v1', route => json(route, {
    schema: 'ananta.dashboard-feature-flags.v1',
    features: {
      angular_kanban: false, angular_model_dashboard: true, model_catalog_v2: true,
      model_routing_editor: true, legacy_model_picker_deprecation: true,
    },
  }));
  await page.route('**/models/catalog/v1', route => json(route, {
    schema: 'ananta.model-catalog.v1', default_selection: null, models: [], provider_failures: [],
  }));
  await page.route('**/models/catalog/v2', route => json(route, {
    schema: 'ananta.model-catalog.v2', catalog_revision: 7, models, sources: [], partial: false,
  }));
  await page.route('**/models/consumers/v1', route => json(route, {
    schema: 'ananta.model-consumer-registry.v1', consumers,
  }));
  await page.route('**/models/routing/v1/effective', route => json(route, {
    schema: 'ananta.effective-model-routing-projection.v1',
    configuration_revision: routing.revision, routes: [],
  }));
  await page.route('**/models/routing/v1/templates', route => json(route, {
    schema: 'ananta.model-routing-template-catalog.v1',
    configuration_revision: routing.revision, templates: [],
  }));
  await page.route('**/models/styles/v1', route => json(route, {
    schema: 'ananta.cognitive-style-read-model.v1',
    configuration: { schema: 'ananta.cognitive-style-configuration.v1', revision: 0, profiles: [], role_targets: [], overlays: [] },
    profile_history: [], mismatch_evidence: [], evolution_proposals: [],
    heuristic_notice: 'Operative Heuristik, keine psychologische Diagnose.',
  }));
  await page.route('**/models/routing/v1/migration/preview', route => json(route, {
    schema: 'ananta.model-routing-legacy-migration-preview.v1', current_revision: 0,
    applicable: true, idempotent: true, confirmation_digest: `sha256:${'a'.repeat(64)}`,
    entries: [{
      consumer_id: 'task.consumer-0', legacy_source: 'default_provider/default_model',
      legacy_provider_id: 'lmstudio', legacy_model_id: 'lmstudio-model',
      matched_profile_id: 'profile-4', status: 'proposed', reason_code: 'legacy_model_profile_matched',
    }],
    proposed_configuration: routing, issues: [],
  }));
  await page.route('**/models/routing/v1/migration/apply', route => {
    migrationCalls += 1;
    return json(route, { ...routing, revision: routing.revision + 1 });
  });
  await page.route('**/models/routing/v1/shadow', route => json(route, {
    schema: 'ananta.model-routing-shadow-report.v1', configuration_revision: 0,
    matches: true, entries: [],
  }));
  await page.route('**/models/routing/v1/release-gate', route => json(route, {
    schema: 'ananta.model-routing-release-gate.v1', configuration_revision: 0,
    ready: true, checks: [{ check_id: 'legacy_shadow_match', passed: true, reason_code: 'legacy_shadow_matches' }],
  }));
  await page.route('**/models/routing/v1/diagnostics', route => json(route, {
    schema: 'ananta.model-routing-diagnostics.v1', generated_at: '2026-08-25T00:00:00Z',
    configuration_revision: 0, catalog_revision: 7, unresolved_assignment_count: 0,
    non_executable_route_count: 0, contains_secrets: false, issues: [], usage: [],
  }));
  await page.route('**/models/routing/v1/validate', async route => json(route, {
    schema: 'ananta.model-routing-validation-report.v1', valid: true,
    expected_revision: routing.revision, current_revision: routing.revision, issues: [],
  }));
  await page.route('**/models/routing/v1', async route => {
    if (route.request().method() === 'GET') return json(route, routing);
    saveCalls += 1;
    if (conflictNext) {
      conflictNext = false;
      return json(route, { status: 'error', message: 'model_routing_revision_conflict', data: { current_revision: routing.revision + 1 } }, 409);
    }
    const body = route.request().postDataJSON();
    routing = {
      schema: 'ananta.model-routing-config.v1', revision: routing.revision + 1,
      assignments: body.assignments, fallback_groups: body.fallback_groups,
    };
    return json(route, routing);
  });

  return {
    saveCalls: () => saveCalls,
    migrationCalls: () => migrationCalls,
    conflictOnNextSave: () => { conflictNext = true; },
  };
}

test('central model settings scale, apply atomically, surface conflicts and gate migration', async ({ page }) => {
  const fixture = await modelSettingsFixtures(page);
  const loadStarted = Date.now();
  await page.goto('/settings?section=models');
  await expect(page.getByTestId('model-dashboard')).toBeVisible();
  expect(Date.now() - loadStarted).toBeLessThan(10_000);
  await expect(page.locator('.model-row')).toHaveCount(60);

  const search = page.getByRole('searchbox', { name: 'Suche' });
  for (const name of ['Codex CLI', 'Claude Code', 'OpenRouter Model', 'Ollama Model', 'LM Studio Model']) {
    await search.fill(name);
    await expect(page.getByText(name, { exact: true })).toBeVisible();
  }
  await search.fill('');

  const assignmentsStarted = Date.now();
  await page.getByRole('tab', { name: 'Zuweisungen' }).click();
  await expect(page.locator('.assignment-table tbody tr').filter({ hasText: 'Consumer 499' })).toBeVisible();
  expect(Date.now() - assignmentsStarted).toBeLessThan(10_000);
  expect(await page.locator('datalist#model-profile-options option').count()).toBe(5000);
  expect(await page.getByLabel(/Profil für Consumer/).count()).toBe(500);

  await page.getByLabel('Modus für Consumer 0').selectOption('profile');
  await page.getByRole('tab', { name: 'Fallbacks' }).click();
  await page.getByLabel('Neue Gruppen-ID').fill('local-first');
  await page.getByRole('button', { name: 'Gruppe anlegen' }).click();
  await page.getByLabel('Profil hinzufügen').selectOption('profile-4');
  await page.getByRole('button', { name: 'Hinzufügen' }).click();
  await expect(page.locator('.fallback-card code', { hasText: 'profile-4' })).toBeVisible();
  const remove = page.getByRole('button', { name: 'Kandidat entfernen' });
  await remove.focus();
  await expect(remove).toBeFocused();

  await page.getByRole('tab', { name: 'Änderungen' }).click();
  await page.getByRole('button', { name: 'Validieren und atomar speichern' }).click();
  await expect.poll(fixture.saveCalls).toBe(1);
  await expect(page.getByText(/Revision 1 gespeichert/)).toBeVisible();

  await page.getByRole('tab', { name: 'Zuweisungen' }).click();
  await page.getByLabel('Modus für Consumer 0').selectOption('disabled');
  fixture.conflictOnNextSave();
  await page.getByRole('tab', { name: 'Änderungen' }).click();
  await page.getByRole('button', { name: 'Validieren und atomar speichern' }).click();
  await expect(page.getByText(/Hub-Konfiguration wurde zwischenzeitlich geändert/)).toBeVisible();

  await expect(page.getByText('Release-Gate bestanden')).toBeVisible();
  await page.getByLabel(/Vorschau, Revision und Shadow-Abweichungen/).check();
  await page.getByRole('button', { name: /Migration explizit bestätigen/ }).click();
  await expect.poll(fixture.migrationCalls).toBe(1);

  const accessibility = await new AxeBuilder({ page })
    .include('.model-dashboard')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(accessibility.violations).toEqual([]);
});
