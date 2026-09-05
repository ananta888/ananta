import { expect, Page, Route, test } from '@playwright/test';

import { loginFast } from './utils';

type MutationResponse = {
  status: number;
  body: {
    data?: Record<string, unknown>;
    error?: { code?: string };
  };
};

const dataset = {
  id: 'dataset-unsloth-e2e',
  name: 'Unsloth Contract Dataset',
  purpose: 'Fake-Hub release contract',
  license: 'private',
  privacy: 'private',
  format: 'instruction',
  status: 'valid',
  validation_status: 'valid',
  trainable: true,
  sha256: '1'.repeat(64),
  size_bytes: 256,
  record_count: 12,
  accepted_record_count: 12,
  rejected_record_count: 0,
  duplicate_record_count: 0,
  train_record_count: 10,
  validation_record_count: 2,
};

async function json(route: Route, body: unknown, status = 200): Promise<void> {
  await route.fulfill({
    status,
    contentType: 'application/json',
    body: JSON.stringify(body),
  });
}

function stableJson(value: unknown): string {
  const normalize = (current: unknown): unknown => {
    if (Array.isArray(current)) return current.map(normalize);
    if (current && typeof current === 'object') {
      return Object.fromEntries(
        Object.entries(current as Record<string, unknown>)
          .sort(([left], [right]) => left.localeCompare(right))
          .map(([key, child]) => [key, normalize(child)]),
      );
    }
    return current;
  };
  return JSON.stringify(normalize(value));
}

function mutationFingerprint(body: Record<string, unknown>): string {
  const immutable = Object.fromEntries(
    Object.entries(body).filter(([key]) => !['dry_run', 'confirmed', 'confirmation_id'].includes(key)),
  );
  return stableJson(immutable);
}

async function installFakeHub(page: Page): Promise<string[]> {
  const requestUrls: string[] = [];
  const replayLedger = new Map<string, { fingerprint: string; response: Record<string, unknown> }>();
  const confirmations = new Map<string, string>();
  let storageRevision = 7;
  let storageItems = [{
    artifact_id: 'adapter-storage-e2e',
    storage_ref: 'unsloth-storage:adapter-storage-e2e',
    kind: 'export',
    job_id: 'job-unsloth-e2e',
    attempt_id: 'attempt-1',
    sha256: '2'.repeat(64),
    size_bytes: 4096,
    created_at: 1_700_000_000,
    retention_until: 1_700_086_400,
    state: 'active',
    reference_kinds: [],
    referenced: false,
  }];
  const storageReadModel = () => ({
    usage: {
      schema: 'ananta.unsloth-storage-usage.v1',
      catalog_revision: storageRevision,
      usage: {
        export: {
          bytes: storageItems.reduce((total, item) => total + item.size_bytes, 0),
          artifacts: storageItems.length,
        },
      },
      tenant_total_bytes: storageItems.reduce((total, item) => total + item.size_bytes, 0),
      quotas: {
        dataset_bytes: 1_000_000,
        model_bytes: 1_000_000,
        checkpoint_bytes: 1_000_000,
        export_bytes: 1_000_000,
        tenant_total_bytes: 4_000_000,
        retention_seconds: 86_400,
        max_cleanup_items: 100,
      },
      paths_exposed: false,
    },
    items: storageItems,
  });

  await page.route('**/api/ml-intern-training/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname.replace(/^.*\/api\/ml-intern-training/, '');
    const method = request.method();
    requestUrls.push(request.url());

    if (path === '/capabilities' && method === 'GET') {
      return json(route, {
        available: true,
        backends: [{ id: 'unsloth', label: 'Unsloth', available: true }],
        gpu_profiles: [{ id: 'nvidia', label: 'NVIDIA GPU', available: true }],
        base_models: [{
          id: 'local/tiny-causal-lm',
          label: 'Approved local tiny causal LM',
          local: true,
          available: true,
          compatible_backends: ['unsloth'],
        }],
        limits: {
          max_dataset_bytes: 1_000_000,
          max_adapter_bytes: 1_000_000,
          max_lora_rank: 64,
          max_steps: 1000,
        },
        unsloth: {
          status: 'available',
          core: { available: true, reason_code: 'available' },
          studio: { available: false, reason_code: 'studio_transport_not_configured' },
          mcp: { available: false, reason_code: 'mcp_transport_not_configured' },
          modalities: {
            text: { available: true, reason_code: 'available' },
            vision: { available: false, reason_code: 'modality_not_enabled' },
            audio: { available: false, reason_code: 'modality_not_enabled' },
            embedding: { available: false, reason_code: 'modality_not_enabled' },
          },
          operations: {
            export: { available: true, reason_code: 'available' },
            runtime_handoff: { available: true, reason_code: 'available' },
            mcp: { available: false, reason_code: 'mcp_transport_not_configured' },
            cleanup: { available: true, reason_code: 'available' },
          },
        },
      });
    }
    if (path === '/unsloth/storage' && method === 'GET') {
      return json(route, { status: 'success', data: storageReadModel() });
    }
    if (path === '/datasets' && method === 'GET') {
      return json(route, { items: [dataset], count: 1 });
    }
    if (path === '/jobs' && method === 'GET') {
      return json(route, { items: [], count: 0 });
    }
    if (path === '/adapters' && method === 'GET') {
      return json(route, { items: [], count: 0 });
    }

    const mutation = path.match(/^\/unsloth\/mutations\/([a-z_]+)$/);
    if (mutation && method === 'POST') {
      const idempotencyKey = request.headers()['idempotency-key'] || '';
      if (!idempotencyKey) {
        return json(route, { error: { code: 'idempotency_key_required' } }, 400);
      }
      const body = request.postDataJSON() as Record<string, unknown>;
      const operation = mutation[1];
      if (body.operation !== operation) {
        return json(route, { error: { code: 'mutation_operation_mismatch' } }, 422);
      }
      const resourceId = String(body.resource_id || '');
      if (
        !/^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/.test(resourceId)
        || resourceId.includes('://')
        || resourceId.includes('/')
      ) {
        return json(route, { error: { code: 'resource_reference_not_opaque' } }, 422);
      }
      const fingerprint = stableJson(body);
      const replay = replayLedger.get(idempotencyKey);
      if (replay) {
        if (replay.fingerprint !== fingerprint) {
          return json(route, { error: { code: 'idempotency_conflict' } }, 409);
        }
        return json(route, { data: { ...replay.response, replayed: true } });
      }
      if (
        operation === 'cleanup' &&
        (!Array.isArray(body.artifact_ids) ||
          body.artifact_ids.length === 0 ||
          Number(body.expected_catalog_revision) !== storageRevision)
      ) {
        return json(route, { error: { code: 'storage_catalog_revision_conflict' } }, 409);
      }
      if (body.dry_run === true && body.confirmed === false) {
        const confirmationId = `confirmation-${operation}-${resourceId}-${storageRevision}`;
        confirmations.set(confirmationId, mutationFingerprint(body));
        const response = {
          accepted: true,
          operation,
          resource_id: resourceId,
          dry_run: true,
          confirmation_id: confirmationId,
          reason_code: 'dry_run_accepted',
          replayed: false,
        };
        replayLedger.set(idempotencyKey, { fingerprint, response });
        return json(route, { data: response });
      }
      const confirmationId = String(body.confirmation_id || '');
      const dryRunFingerprint = confirmations.get(confirmationId);
      if (
        body.dry_run !== false ||
        body.confirmed !== true ||
        !dryRunFingerprint ||
        dryRunFingerprint !== mutationFingerprint(body)
      ) {
        return json(route, { error: { code: 'unsloth_confirmation_invalid' } }, 409);
      }
      if (operation === 'cleanup') {
        const selected = new Set(body.artifact_ids as string[]);
        storageItems = storageItems.map(item => selected.has(item.artifact_id)
          ? { ...item, state: 'cleanup_queued', cleanup_task_id: 'task-storage-cleanup-e2e' }
          : item);
        storageRevision += 1;
      }
      const response = {
        accepted: true,
        operation,
        resource_id: resourceId,
        dry_run: false,
        reason_code: 'mutation_applied',
        replayed: false,
      };
      replayLedger.set(idempotencyKey, { fingerprint, response });
      return json(route, { data: response }, 201);
    }
    return json(route, { error: { code: 'fake_hub_route_missing' } }, 404);
  });
  return requestUrls;
}

async function mutation(
  page: Page,
  payload: Record<string, unknown>,
  idempotencyKey?: string,
): Promise<MutationResponse> {
  return page.evaluate(async ({ body, key }) => {
    const response = await fetch('/api/ml-intern-training/unsloth/mutations/export', {
      method: 'POST',
      credentials: 'same-origin',
      headers: {
        'Content-Type': 'application/json',
        ...(key ? { 'Idempotency-Key': key } : {}),
      },
      body: JSON.stringify(body),
    });
    return { status: response.status, body: await response.json() };
  }, { body: payload, key: idempotencyKey });
}

test.describe('Unsloth model-training Fake-Hub contract', () => {
  test('renders capability and enforces dry-run, confirmation, replay and denial boundaries', async ({ page, request }) => {
    await loginFast(page, request);
    const requestUrls = await installFakeHub(page);
    await page.goto('/model-training');

    await expect(page.getByTestId('model-training-control-center')).toBeVisible();
    await page.getByRole('tab', { name: 'Training starten' }).click();
    await page.getByTestId('training-wizard-dataset').selectOption(dataset.id);
    await page.getByRole('button', { name: 'Weiter' }).click();
    await expect(page.getByTestId('training-wizard-backend').locator('option[value="unsloth"]')).toHaveText(/Unsloth/i);

    const capability = await page.evaluate(async () => {
      const response = await fetch('/api/ml-intern-training/capabilities');
      return response.json();
    });
    expect(capability.unsloth.operations.export).toEqual({
      available: true,
      reason_code: 'available',
    });
    expect(capability.unsloth.operations.runtime_handoff.available).toBe(true);
    expect(capability.unsloth.studio.reason_code).toBe('studio_transport_not_configured');
    expect(capability.unsloth.mcp.reason_code).toBe('mcp_transport_not_configured');
    expect(capability.unsloth.operations.cleanup.available).toBe(true);

    const storageEnvelope = await page.evaluate(async () => {
      const response = await fetch('/api/ml-intern-training/unsloth/storage');
      return response.json();
    });
    expect(storageEnvelope.data.usage.paths_exposed).toBe(false);
    expect(storageEnvelope.data.items[0]).toMatchObject({
      artifact_id: 'adapter-storage-e2e',
      kind: 'export',
      job_id: 'job-unsloth-e2e',
      attempt_id: 'attempt-1',
    });
    expect(JSON.stringify(storageEnvelope)).not.toMatch(/filesystem_path|relative_ref|\/tmp\//);

    const unslothPanel = page.locator('app-unsloth-capability-panel');
    await unslothPanel.getByLabel('Storage-Artefakt adapter-storage-e2e für Cleanup auswählen').check();
    await unslothPanel.getByLabel('Unsloth Operation').selectOption('cleanup');
    await unslothPanel.getByLabel('Begründung').fill('Retention cleanup through the Hub');
    await unslothPanel.getByRole('button', { name: 'Dry-Run über Hub' }).click();
    await expect(unslothPanel.getByText('Vom Hub akzeptiert')).toBeVisible();
    await unslothPanel.getByLabel('Dry-Run-Zusammenfassung geprüft').check();
    await unslothPanel.getByRole('button', { name: 'Mutation bestätigen' }).click();
    await expect(unslothPanel.getByText('cleanup_queued')).toBeVisible();

    const dryPayload = {
      operation: 'export',
      resource_id: 'adapter-e2e',
      dry_run: true,
      confirmed: false,
      reason: 'Release-gate export',
      format: 'gguf:q4_k_m',
    };
    const dryRun = await mutation(page, dryPayload, 'dry-run-key');
    expect(dryRun.status).toBe(200);
    const confirmationId = String(dryRun.body.data?.confirmation_id);

    const confirmPayload = {
      ...dryPayload,
      dry_run: false,
      confirmed: true,
      confirmation_id: confirmationId,
    };
    const confirmed = await mutation(page, confirmPayload, 'confirm-key');
    expect(confirmed.status).toBe(201);
    expect(confirmed.body.data?.reason_code).toBe('mutation_applied');

    const replay = await mutation(page, confirmPayload, 'confirm-key');
    expect(replay.status).toBe(200);
    expect(replay.body.data?.replayed).toBe(true);

    const missingKey = await mutation(page, dryPayload);
    expect(missingKey.status).toBe(400);
    expect(missingKey.body.error?.code).toBe('idempotency_key_required');

    const directPath = await mutation(page, {
      ...dryPayload,
      resource_id: '/tmp/direct-worker-artifact',
    }, 'direct-path-key');
    expect(directPath.status).toBe(422);
    expect(directPath.body.error?.code).toBe('resource_reference_not_opaque');

    const changedConfirmation = await mutation(page, {
      ...confirmPayload,
      reason: 'Changed after dry-run',
    }, 'changed-confirmation-key');
    expect(changedConfirmation.status).toBe(409);
    expect(changedConfirmation.body.error?.code).toBe('unsloth_confirmation_invalid');

    await mutation(page, dryPayload, 'conflict-key');
    const conflict = await mutation(page, {
      ...dryPayload,
      reason: 'Different payload under the same key',
    }, 'conflict-key');
    expect(conflict.status).toBe(409);
    expect(conflict.body.error?.code).toBe('idempotency_conflict');

    expect(requestUrls.length).toBeGreaterThan(0);
    expect(requestUrls.every(url => url.includes('/api/ml-intern-training/'))).toBe(true);
    expect(requestUrls.some(url => /worker|studio|localhost:\d+\/unsloth/i.test(url))).toBe(false);
  });
});
