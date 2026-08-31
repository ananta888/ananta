import AxeBuilder from '@axe-core/playwright';
import { expect, Page, Route, test } from '@playwright/test';

import { loginFast } from './utils';

type AdapterState = 'evaluated' | 'approved' | 'deprecated';
const exportSha256 = 'e'.repeat(64);

const dataset = {
  id: 'dataset-e2e', name: 'E2E Training', purpose: 'Mock Dry-run', license: 'private', privacy: 'private',
  format: 'instruction', status: 'valid', validation_status: 'valid', trainable: true,
  sha256: '1234567890abcdef', size_bytes: 128, record_count: 10,
  accepted_record_count: 10, rejected_record_count: 0, duplicate_record_count: 0,
  train_record_count: 8, validation_record_count: 2,
  validation_report: {
    dataset_id: 'dataset-e2e', valid: true, trainable: true, total_records: 10,
    accepted_records: 10, rejected_records: 0, duplicate_records: 0,
    secret_findings: 0, pii_findings: 0, train_records: 8, validation_records: 2, issues: [],
  },
};

const validationDataset = {
  ...dataset,
  id: 'dataset-validation-e2e', name: 'E2E Separate Validation', purpose: 'Externe Validation',
  sha256: 'fedcba0987654321', record_count: 4, train_record_count: 3, validation_record_count: 1,
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
}

async function expectTrainingAxeClean(page: Page): Promise<void> {
  const accessibility = await new AxeBuilder({ page })
    .include('.training-control-center')
    .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa'])
    .analyze();
  expect(accessibility.violations).toEqual([]);
}

async function installTrainingMock(page: Page) {
  let uploaded = false;
  let jobCreated = false;
  let adapterStatus: AdapterState = 'evaluated';
  const lifecycleActions: string[] = [];
  const evaluationScorers: string[] = [];
  let exportRequests = 0;
  const runtimeActions: Array<{ action: string; body: Record<string, unknown> }> = [];
  const dendriticRequests: Array<{ path: string; body: Record<string, unknown> }> = [];

  await page.route('**/api/ml-intern-training/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^.*\/api\/ml-intern-training/, '');
    const method = request.method();

    if (path === '/capabilities' && method === 'GET') return json(route, {
      available: true,
      backends: [{ id: 'mock', available: true }],
      gpu_profiles: [{ id: 'none', label: 'CPU Mock', available: true }],
      base_models: [{ id: 'local-model', label: 'Local Mock Model', local: true, available: true, compatible_backends: ['mock'] }],
      limits: { max_dataset_bytes: 1_000_000, max_adapter_bytes: 1_000_000, max_lora_rank: 64, max_steps: 1000 },
      dendritic_memory_experiment: {
        schema: 'ananta.dendritic-memory-capability.v1', state: 'available', available: true,
        experimental: true, not_production_ready: true, claims_not_verified: true,
        limits: { max_pack_bytes: 1_048_576, max_branches: 64, max_hidden_dimension: 4096, max_steps: 1000 },
        human_intervention_required: false,
      },
    });
    if (path === '/dendritic-memory/dry-run' && method === 'POST') return json(route, {
      admissible: true, reason_codes: [], spec_digest: 'a'.repeat(64),
      model_download_performed: false, worker_call_performed: false, human_intervention_required: false,
    });
    if (path === '/dendritic-memory/runs' && method === 'POST') {
      dendriticRequests.push({ path, body: request.postDataJSON() as Record<string, unknown> });
      return json(route, {
        run_id: 'dendritic-run-e2e', state: 'queued', revision: 1, replayed: false,
        experimental: true, not_production_ready: true, claims_not_verified: true,
        human_intervention_required: false,
      });
    }
    if (path === '/dendritic-memory/runs' && method === 'GET') return json(route, { items: [{
      run_id: 'dendritic-run-e2e', attempt_id: 'attempt-e2e', fencing_token: 1,
      state: 'running', revision: 2, reason_code: 'dendritic_worker_claimed', updated_at: '2026-08-31T00:00:00Z',
      experimental: true, not_production_ready: true, claims_not_verified: true,
      human_intervention_required: false, replayed: false, result: null,
    }], limit: 100 });
    if (path === '/dendritic-memory/packs' && method === 'GET') return json(route, { items: [{
      pack_digest: 'f'.repeat(64), state: 'approved_for_experiment', revision: 2,
      reason_code: 'dendritic_pack_approved_by_policy', experimental: true, production_eligible: false,
      manifest: { base_model_id: 'mock-local-model', base_model_snapshot_digest: 'b'.repeat(64),
        parent_pack_digests: [], target_layers: ['model.layers.0'] },
    }], limit: 100 });
    if (path === `/dendritic-memory/packs/${'f'.repeat(64)}/revoke` && method === 'POST') {
      dendriticRequests.push({ path, body: request.postDataJSON() as Record<string, unknown> });
      return json(route, { pack_digest: 'f'.repeat(64), state: 'revoked', revision: 3 });
    }
    if (path === '/datasets' && method === 'GET') return json(route, {
      items: uploaded ? [dataset, validationDataset] : [validationDataset], count: uploaded ? 2 : 1,
    });
    if (path === '/datasets' && method === 'POST') {
      uploaded = true;
      return json(route, { dataset });
    }
    if (path === '/datasets/dataset-e2e' && method === 'GET') return json(route, { dataset });
    if (path === '/datasets/dataset-e2e' && method === 'DELETE') return json(route, {
      status: 'error', data: { error: { code: 'dataset_referenced', message: 'referenced datasets cannot be deleted' } },
    }, 409);
    if (path === '/datasets/dataset-e2e/validation-dataset' && method === 'POST') return json(route, {
      dataset: {
        ...dataset,
        validation_record_count: 4,
        external_validation: {
          dataset_id: validationDataset.id,
          semantic_overlap_count: 0,
          algorithm_version: 'external-validation-dataset-v1',
        },
      },
    });
    if (path === '/datasets/dataset-e2e/records' && method === 'GET') return json(route, {
      items: [{ index: 0, split: url.searchParams.get('split') || 'train', instruction: 'bounded prompt', output: 'bounded output', valid: true }],
      count: 1, next_cursor: null,
    });
    if (path === '/datasets/dataset-e2e/split' && method === 'POST') return json(route, { dataset });
    if (path === '/datasets/dataset-e2e/validate' && method === 'POST') return json(route, dataset.validation_report);

    if (path === '/jobs' && method === 'GET') return json(route, {
      items: jobCreated ? [{
        id: 'job-e2e', status: 'completed', phase: 'published', dataset_id: dataset.id,
        dataset_name: dataset.name, base_model_id: 'local-model', backend: 'mock', mode: 'dry_run',
        progress_percent: 100, current_step: 10, max_steps: 10, created_at: 1_700_000_000,
      }] : [],
      count: jobCreated ? 1 : 0,
    });
    if (path === '/jobs' && method === 'POST') {
      jobCreated = true;
      return json(route, { job_id: 'job-e2e', status: 'queued' }, 202);
    }
    if (path === '/jobs/job-e2e' && method === 'GET') return json(route, {
      id: 'job-e2e', status: 'completed', phase: 'published', dataset_id: dataset.id,
      dataset_name: dataset.name, base_model_id: 'local-model', backend: 'mock', mode: 'dry_run',
      progress_percent: 100, current_step: 10, max_steps: 10, latest_train_loss: 0.2,
      latest_eval_loss: 0.3, cancellable: false, metrics: [{ step: 10, max_steps: 10, train_loss: 0.2, eval_loss: 0.3, learning_rate: 0.0002 }],
    });
    if (path === '/jobs/job-e2e/events' && method === 'GET') return json(route, {
      items: [{ sequence: 1, event_type: 'completed', phase: 'published', message: 'Dry-run completed' }],
      count: 1, next_sequence: 1,
    });

    if (path === '/adapters' && method === 'GET') return json(route, { items: [{
      id: 'adapter-e2e', name: 'E2E Adapter', version: lifecycleActions.length + 1,
      base_model_id: 'local-model', method: 'qlora', status: adapterStatus,
      score: 0.9, sha256: 'abcdef1234567890', active: adapterStatus === 'approved',
      hash_verified: true, artifact_exists: true, evaluation_id: 'evaluation-e2e',
    }], count: 1 });
    if (path === '/evaluations' && method === 'POST') {
      evaluationScorers.push(String(request.postDataJSON()?.scorer_name || ''));
      return json(route, {
      id: 'evaluation-e2e', adapter_id: 'adapter-e2e', dataset_id: dataset.id,
      status: 'completed', passed: true, aggregate_score: 0.9,
      metrics: [{ name: 'accuracy', base_value: 0.5, adapter_value: 0.9, delta: 0.4, threshold: 0.1, passed: true }],
      samples: [{ id: 'sample-1', base_output: 'base', adapter_output: 'adapter', expected_output: 'expected', winner: 'adapter' }],
      });
    }
    if (path === '/evaluations/evaluation-e2e' && method === 'GET') return json(route, {
      id: 'evaluation-e2e', adapter_id: 'adapter-e2e', dataset_id: dataset.id,
      status: 'completed', passed: true, metrics: [], samples: [],
    });
    if (path === '/adapters/adapter-e2e/export' && method === 'POST') {
      exportRequests += 1;
      return json(route, {
        artifact_id: 'lora-export-e2e', sha256: exportSha256, size_bytes: 8,
        download_url: '/api/ml-intern-training/exports/lora-export-e2e',
      }, 201);
    }
    if (path === '/exports/lora-export-e2e' && method === 'GET') return route.fulfill({
      status: 200,
      contentType: 'application/zip',
      headers: {
        'Cache-Control': 'no-store',
        'Content-Disposition': 'attachment; filename="lora-export-e2e.zip"',
        'X-Artifact-SHA256': exportSha256,
      },
      body: Buffer.from('PK\u0005\u0006\u0000\u0000'),
    });
    const lifecycle = path.match(/^\/adapters\/adapter-e2e\/(approve|reject|deprecate|rollback)$/);
    if (lifecycle && method === 'POST') {
      lifecycleActions.push(lifecycle[1]);
      adapterStatus = lifecycle[1] === 'approve' || lifecycle[1] === 'rollback' ? 'approved' : 'deprecated';
      return json(route, {
        id: 'adapter-e2e', name: 'E2E Adapter', version: lifecycleActions.length + 1,
        base_model_id: 'local-model', method: 'qlora', status: adapterStatus,
        active: adapterStatus === 'approved', hash_verified: true, artifact_exists: true,
      });
    }
    return json(route, { reason_code: 'mock_route_missing', message: `${method} ${path}` }, 404);
  });

  await page.route('**/api/ml-intern-lora-runtime/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^.*\/api\/ml-intern-lora-runtime/, '');
    const match = path.match(/^\/adapters\/adapter-e2e\/(unload|rollback)$/);
    if (!match || request.method() !== 'POST') {
      return json(route, { reason_code: 'mock_runtime_route_missing', message: `${request.method()} ${path}` }, 404);
    }
    const body = request.postDataJSON() as Record<string, unknown>;
    runtimeActions.push({ action: match[1], body });
    if (match[1] === 'unload') return json(route, {
      adapter_id: 'adapter-e2e', adapter_version: 1, status: 'succeeded', reason_code: 'adapter_cache_unloaded',
    });
    return json(route, {
      adapter_id: 'adapter-e2e', version: 1, status: 'deprecated',
      rollback_target: { type: 'base_model_only', base_model: 'local-model' },
      cache_unload: { adapter_id: 'adapter-e2e', status: 'succeeded', reason_code: 'adapter_cache_unloaded' },
    });
  });

  return {
    lifecycleActions: () => [...lifecycleActions],
    evaluationScorers: () => [...evaluationScorers],
    exportRequests: () => exportRequests,
    runtimeActions: () => [...runtimeActions],
    dendriticRequests: () => [...dendriticRequests],
  };
}

test.describe('Model training control center', () => {
  test('dendritic flow starts live and manages packs without human-in-the-loop', async ({ page, request }) => {
    await loginFast(page, request);
    const mock = await installTrainingMock(page);
    await page.goto('/model-training');
    await page.getByRole('tab', { name: 'Training starten' }).click();
    await expect(page.getByRole('heading', { name: 'Dendritic Memory Experiment' })).toBeVisible();
    await page.getByLabel('Dataset-Manifest SHA-256').fill('a'.repeat(64));
    await page.getByLabel('Modell-Snapshot SHA-256').fill('b'.repeat(64));
    await page.getByRole('button', { name: 'Automatisch starten' }).click();
    await expect(page.getByText(/dendritic-run-e2e wurde vom Hub angenommen/)).toBeVisible();
    await expect(page.getByText(/dendritic_worker_claimed/)).toBeVisible();
    await expect(page.getByText('Produktiv aktivieren ist für experimentelle Memory Packs nicht verfügbar.')).toBeVisible();
    await page.getByRole('button', { name: 'Experiment-Pack widerrufen' }).click();
    await expect.poll(mock.dendriticRequests).toHaveLength(2);
    const create = mock.dendriticRequests()[0].body as { spec: Record<string, unknown> };
    expect(create.spec.mode).toBe('live');
    expect(JSON.stringify(create)).not.toMatch(/confirm|approval|human/i);
  });

  test('admin navigates through an accessible CPU dry-run from upload to approval and rollback', async ({ page, request }) => {
    test.setTimeout(120_000);
    await loginFast(page, request);
    const mock = await installTrainingMock(page);

    const configurationMenu = page.locator('.app-nav details').filter({ hasText: 'Konfiguration' });
    await configurationMenu.locator('summary').click();
    await configurationMenu.getByRole('link', { name: /Modelltraining/ }).click();
    await expect(page).toHaveURL(/\/model-training$/);
    await expect(page.getByTestId('model-training-control-center')).toBeVisible();
    await expect(page.locator('app-breadcrumb')).toContainText('Modelltraining');

    const tabs = page.getByRole('tab');
    await tabs.first().focus();
    await page.keyboard.press('ArrowRight');
    await expect(page.getByRole('tab', { name: 'Training starten' })).toHaveAttribute('aria-selected', 'true');
    await page.getByRole('tab', { name: 'Datasets' }).click();

    await page.getByTestId('training-dataset-file').setInputFiles({
      name: 'training.jsonl', mimeType: 'application/x-ndjson',
      buffer: Buffer.from('{"instruction":"hello","output":"world"}\n'),
    });
    await page.getByLabel('Zweck').fill('Lokaler E2E Dry-run');
    await page.getByLabel('Lizenz').fill('private');
    await page.getByTestId('training-dataset-upload').click();
    await expect(page.getByRole('button', { name: 'Dataset E2E Training öffnen' })).toBeVisible();
    await page.getByRole('button', { name: 'Dataset E2E Training öffnen' }).press('Enter');
    await expect(page.getByRole('heading', { name: 'E2E Training' })).toBeVisible();
    await page.getByLabel('Separat hochgeladenes Validation-Dataset').selectOption(validationDataset.id);
    await page.getByLabel(/Ich bestätige, dass der bestehende Validation-Split/).check();
    await page.getByRole('button', { name: 'Validation-Dataset anhängen' }).click();
    await expect(page.getByText('external-validation-dataset-v1')).toBeVisible();
    await page.getByLabel(/Ich bestätige die dauerhafte Löschung/).check();
    await page.getByRole('button', { name: 'Dataset endgültig löschen' }).click();
    await expect(page.getByRole('alert').filter({ hasText: 'dataset_referenced' })).toBeVisible();
    await expect(page.getByText(/Force-Delete wird nicht angeboten/).first()).toBeVisible();
    await page.getByRole('button', { name: 'Split anwenden' }).click();
    await expect(page.getByRole('dialog', { name: 'Vorhandenen Split ersetzen?' })).toBeVisible();
    await expectTrainingAxeClean(page);
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Vorhandenen Split ersetzen?' })).toBeHidden();

    await page.getByRole('tab', { name: 'Training starten' }).click();
    await page.getByTestId('training-wizard-dataset').selectOption(dataset.id);
    await page.getByRole('button', { name: 'Weiter' }).click();
    await page.getByLabel('Basismodell').selectOption('local-model');
    await page.getByLabel('Backend').selectOption('mock');
    await page.getByLabel('GPU-Profil').selectOption('none');
    await page.getByRole('button', { name: 'Weiter' }).click();
    await page.getByRole('button', { name: 'Weiter' }).click();
    await expect(page.getByText('Sicherer Standard')).toBeVisible();
    await expectTrainingAxeClean(page);
    await page.getByRole('button', { name: 'Job in Hub-Queue einstellen' }).click();
    await expect(page.getByText('Job job-e2e')).toBeVisible();
    await expect(page.getByText('published', { exact: true }).first()).toBeVisible();
    await expectTrainingAxeClean(page);

    await page.getByRole('tab', { name: 'Adapter & Evaluation' }).click();
    await page.getByRole('button', { name: 'Adapter E2E Adapter Version 1 öffnen' }).click();
    await page.getByLabel('Validation-Dataset').selectOption(dataset.id);
    await page.getByLabel('Scorer').selectOption('ananta_todo_json');
    await page.getByRole('button', { name: 'Evaluation starten' }).click();
    await expect(page.getByText('accuracy', { exact: true })).toBeVisible();
    await expect.poll(mock.evaluationScorers).toEqual(['ananta_todo_json']);
    const registryLifecycle = page.getByRole('region', { name: /E2E Adapter v\d+/ });
    await registryLifecycle.getByRole('textbox', { name: /Begründung/ }).fill('Evaluation erfolgreich');
    await page.getByLabel(/Ich bestätige die explizite Registry-Änderung/).check();
    await page.getByRole('button', { name: 'Aktion ausführen' }).click();
    await expect.poll(mock.lifecycleActions).toEqual(['approve']);

    const exportButton = page.getByRole('button', { name: 'Hashverifiziert exportieren' });
    await expect(exportButton).toBeEnabled();
    const [exportResponse] = await Promise.all([
      page.waitForResponse(response => response.url().endsWith('/adapters/adapter-e2e/export')),
      exportButton.click(),
    ]);
    expect(exportResponse.status()).toBe(201);
    expect(await exportResponse.json()).toMatchObject({ artifact_id: 'lora-export-e2e', sha256: exportSha256 });
    await expect.poll(mock.exportRequests).toBe(1);
    await expect(page.getByText(/Export-Artifact lora-export-e2e/)).toBeVisible();
    const exportDownload = page.waitForEvent('download');
    await page.getByRole('button', { name: 'ZIP authentifiziert herunterladen' }).click();
    await expect((await exportDownload).suggestedFilename()).toBe('lora-export-e2e.zip');

    await page.getByLabel('Runtime-Aktion').selectOption('unload');
    await page.getByLabel('Operative Begründung').fill('Operator entlädt den GPU Cache kontrolliert');
    await page.getByLabel(/Ich bestätige dieses Admin-Runtime-Kommando/).check();
    await page.getByRole('button', { name: 'Runtime-Cache jetzt entladen' }).click();
    await page.getByLabel('Runtime-Aktion').selectOption('rollback');
    await page.getByLabel('Operative Begründung').fill('Regression verlangt sicheren Runtime Fallback');
    await page.getByLabel(/Ich bestätige dieses Admin-Runtime-Kommando/).check();
    await page.getByRole('button', { name: 'Runtime-Rollback jetzt ausführen' }).click();
    await expect.poll(mock.runtimeActions).toEqual([
      { action: 'unload', body: { confirmed: true, reason: 'Operator entlädt den GPU Cache kontrolliert' } },
      {
        action: 'rollback',
        body: {
          confirmed: true,
          reason: 'Regression verlangt sicheren Runtime Fallback',
          expected_version: 2,
        },
      },
    ]);

    await page.getByLabel('Lifecycle-Aktion').selectOption('rollback');
    await registryLifecycle.getByRole('textbox', { name: /Begründung/ }).fill('Rollback kontrolliert');
    await page.getByLabel(/Ich bestätige die explizite Registry-Änderung/).check();
    await page.getByRole('button', { name: 'Aktion ausführen' }).click();
    await expect.poll(mock.lifecycleActions).toEqual(['approve', 'rollback']);

    await expectTrainingAxeClean(page);

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/workspace');
    await page.getByRole('button', { name: 'Navigation' }).click();
    const mobileConfiguration = page.locator('.app-nav.nav-open details').filter({ hasText: 'Konfiguration' });
    await mobileConfiguration.locator('summary').click();
    await mobileConfiguration.getByRole('link', { name: /Modelltraining/ }).click();
    await expect(page.getByTestId('model-training-control-center')).toBeVisible();
  });
});
