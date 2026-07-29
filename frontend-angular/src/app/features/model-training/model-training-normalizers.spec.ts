import {
  normalizeAdapter,
  normalizeDataset,
  normalizeDatasetRecord,
  normalizePage,
  normalizeTrainingEventPage,
  normalizeTrainingJob,
  normalizeTrainingJobAcceptance,
  normalizeUnslothStorage,
} from './model-training-normalizers';

describe('model training backend-v2 read-model normalization', () => {
  it('keeps immutable adapter and monotone registry versions separate', () => {
    const adapter = normalizeAdapter({
      id: 'adapter-v1',
      name: 'Adapter',
      version: 4,
      adapter_version: 'artifact-4',
      registry_version: 9,
      base_model_id: 'local/base',
      status: 'evaluated',
    });

    expect(adapter.version).toBe(4);
    expect(adapter.adapter_version).toBe('artifact-4');
    expect(adapter.registry_version).toBe(9);
  });

  it('projects catalog dataset aliases, partitions and validation summary into the UI contract', () => {
    const dataset = normalizeDataset({
      dataset_id: 'dataset-v2',
      name: 'Backend v2 dataset',
      status: 'validated',
      format_type: 'instruction',
      record_count: 12,
      duplicate_count: 2,
      dataset_bytes: 4096,
      dataset_sha256: 'abc123',
      partitions: {
        train: { record_count: 10 },
        validation: { record_count: 2 },
      },
      split: { algorithm_version: 'v1', seed: 42, validation_ratio: 0.2 },
      external_validation: {
        dataset_id: 'validation-2', semantic_overlap_count: 0, algorithm_version: 'external-validation-dataset-v1',
      },
      validation: {
        status: 'passed',
        trainable: true,
        summary: { warning_count: 1, secret_finding_count: 0, pii_finding_count: 0 },
      },
      created_at: '2026-07-16T08:00:00+00:00',
    });

    expect(dataset).toMatchObject({
      id: 'dataset-v2', format: 'instruction', validation_status: 'valid', trainable: true,
      train_record_count: 10, validation_record_count: 2, duplicate_record_count: 2,
      size_bytes: 4096, sha256: 'abc123',
    });
    expect(dataset.split).toMatchObject({ seed: 42, validation_ratio: 0.2, train_count: 10, validation_count: 2 });
    expect(dataset.external_validation).toEqual({
      dataset_id: 'validation-2', semantic_overlap_count: 0, algorithm_version: 'external-validation-dataset-v1',
    });
    expect(dataset.validation_report?.issues).toContainEqual({ code: 'validation_warnings', severity: 'warning', count: 1 });
    expect(dataset.created_at).toBe(1_784_188_800);
  });

  it('unwraps paginated preview records without exposing wrapper internals as content', () => {
    const page = normalizePage({
      records: [{
        record_index: 7,
        state: 'ready',
        record: { instruction: 'Prompt', input: 'Context', output: 'Answer' },
      }],
      returned_count: 1,
      next_cursor: 'cursor-2',
    }, ['records'], item => normalizeDatasetRecord(item, 'validation'));

    expect(page.count).toBe(1);
    expect(page.next_cursor).toBe('cursor-2');
    expect(page.items[0]).toMatchObject({
      index: 7, split: 'validation', instruction: 'Prompt', input: 'Context', output: 'Answer', valid: true,
    });
  });

  it('maps base_model and flat job metrics while preserving canonical aliases', () => {
    const job = normalizeTrainingJob({
      job_id: 'job-v2', task_id: 'task-v2', dataset_id: 'dataset-v2', base_model: 'model-v2',
      backend: 'peft_trl', status: 'running', phase: 'train', progress_percent: 25,
      current_step: 5, max_steps: 20, train_loss: 1.25, eval_loss: 1.1,
      configuration: { base_model: 'model-v2' },
    });

    expect(job).toMatchObject({
      id: 'job-v2', base_model_id: 'model-v2', latest_train_loss: 1.25,
      latest_eval_loss: 1.1, current_step: 5, max_steps: 20,
    });
  });

  it('flattens type/payload events into cursor-safe progress and metric events', () => {
    const page = normalizeTrainingEventPage({
      items: [{
        sequence: 4,
        type: 'training_progress',
        payload: { phase: 'train', progress_percent: 40, current_step: 8, max_steps: 20, train_loss: 0.9 },
        created_at: 100,
      }],
      count: 1,
      next_sequence: 4,
    });

    expect(page.next_sequence).toBe(4);
    expect(page.items[0]).toMatchObject({
      sequence: 4, event_type: 'training_progress', phase: 'train', progress_percent: 40,
      metric: { step: 8, max_steps: 20, train_loss: 0.9 },
    });
  });

  it('accepts either id or job_id in asynchronous create responses', () => {
    expect(normalizeTrainingJobAcceptance({ id: 'job-v2', task_id: 'task-v2', status: 'queued' })).toEqual({
      job_id: 'job-v2', task_id: 'task-v2', status: 'queued', poll_url: undefined,
      events_url: undefined, idempotent_replay: undefined,
    });
  });

  it('allowlists the public Unsloth storage readmodel without retaining path fields', () => {
    const storage = normalizeUnslothStorage({
      usage: {
        schema: 'ananta.unsloth-storage-usage.v1',
        catalog_revision: 4,
        usage: { export: { bytes: 512, artifacts: 1 } },
        tenant_total_bytes: 512,
        quotas: {
          dataset_bytes: 1024,
          model_bytes: 1024,
          checkpoint_bytes: 1024,
          export_bytes: 1024,
          tenant_total_bytes: 4096,
          retention_seconds: 3600,
          max_cleanup_items: 10,
        },
        paths_exposed: false,
        filesystem_path: '/srv/private/tenant',
      },
      items: [{
        artifact_id: 'artifact-storage-1',
        storage_ref: 'unsloth-storage:artifact-storage-1',
        kind: 'export',
        job_id: 'job-1',
        attempt_id: 'attempt-1',
        sha256: 'a'.repeat(64),
        size_bytes: 512,
        state: 'active',
        reference_kinds: [],
        referenced: false,
        relative_ref: 'tenants/private/jobs/job-1',
      }],
    });

    expect(storage.usage.catalog_revision).toBe(4);
    expect(storage.items[0]).toMatchObject({
      artifact_id: 'artifact-storage-1',
      kind: 'export',
      size_bytes: 512,
    });
    expect(JSON.stringify(storage)).not.toContain('/srv/private');
    expect(JSON.stringify(storage)).not.toContain('relative_ref');
  });
});
