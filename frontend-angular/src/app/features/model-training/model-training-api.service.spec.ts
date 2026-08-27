import { HttpEventType, HttpHeaders, HttpResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import { ModelTrainingApiService } from './model-training-api.service';
import { CreateTrainingJobRequest } from './model-training.models';

describe('ModelTrainingApiService', () => {
  const core = {
    get: vi.fn(() => of({ items: [], count: 0 })),
    post: vi.fn(() => of({})),
    request: vi.fn(() => of({})),
    requestBlob: vi.fn(() => of(new HttpResponse({
      body: new Blob(['zip'], { type: 'application/zip' }),
      headers: new HttpHeaders({ 'X-Artifact-SHA256': 'abc123' }),
    }))),
    currentUserToken: vi.fn(() => 'hub-user-token'),
    requestEvents: vi.fn(() => of(
      { type: HttpEventType.UploadProgress, loaded: 50, total: 100 },
      { type: HttpEventType.Response, body: { status: 'ok', data: { id: 'dataset-1' } } },
    )),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        ModelTrainingApiService,
        { provide: HubApiCoreService, useValue: core },
      ],
    });
  });

  it('requests advisory backend selection only through the Hub endpoint', () => {
    const service = TestBed.inject(ModelTrainingApiService);
    const payload = {
      objective: 'sft' as const,
      method: 'qlora' as const,
      modality: 'text' as const,
      resource_profile: 'rtx3080-safe',
      estimated_model_bytes: 0,
      runtime_budget_seconds: 3600,
      export_format: 'adapter' as const,
    };

    service.recommendBackend('http://hub.test/', payload).subscribe();

    expect(core.post).toHaveBeenCalledWith(
      'http://hub.test/api/ml-intern-training/backends/recommendation',
      payload,
      'http://hub.test/',
    );
  });

  it('reads filtered datasets and cursor events only through the Hub facade', () => {
    const service = TestBed.inject(ModelTrainingApiService);

    service.listDatasets('http://hub.test/', { q: 'code data', status: 'valid', limit: 25 }).subscribe();
    service.listJobEvents('http://hub.test', 'job/7', 41, 900).subscribe();

    expect(core.get.mock.calls[0][0]).toBe(
      'http://hub.test/api/ml-intern-training/datasets?q=code+data&status=valid&limit=25',
    );
    expect(core.get.mock.calls[0].slice(1)).toEqual(['http://hub.test/', undefined, false]);
    expect(core.get.mock.calls[1][0]).toBe(
      'http://hub.test/api/ml-intern-training/jobs/job%2F7/events?after_sequence=41&limit=500',
    );
    expect(String(core.get.mock.calls.flatMap(call => call))).not.toContain('worker');
  });

  it('reads the path-free Unsloth storage model only through the Hub API', () => {
    const service = TestBed.inject(ModelTrainingApiService);

    service.unslothStorage('http://hub.test/').subscribe();

    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/api/ml-intern-training/unsloth/storage',
      'http://hub.test/',
      undefined,
      false,
    );
  });

  it('uploads dataset metadata as FormData with a bounded Hub request and idempotency key', () => {
    const service = TestBed.inject(ModelTrainingApiService);
    const file = new File(['{"instruction":"x","output":"y"}'], 'training.jsonl', { type: 'application/x-ndjson' });

    service.uploadDataset('http://hub.test', {
      file,
      name: ' Code ',
      purpose: ' Local assistant ',
      license: ' private ',
      privacy: 'internal',
      validation_ratio: 0.2,
      split_seed: 42,
    }, 'dataset-key').subscribe();

    const [method, url, hubUrl, options] = core.request.mock.calls[0];
    expect([method, url, hubUrl]).toEqual([
      'POST',
      'http://hub.test/api/ml-intern-training/datasets',
      'http://hub.test',
    ]);
    expect(options.headers).toEqual({ 'Idempotency-Key': 'dataset-key' });
    expect(options.timeoutMs).toBe(120_000);
    expect(options.body).toBeInstanceOf(FormData);
    expect(options.body.get('file')).toMatchObject({ name: file.name, size: file.size });
    expect(options.body.get('name')).toBe('Code');
    expect(options.body.get('validation_ratio')).toBe('0.2');
    expect(options.body.get('split_seed')).toBe('42');
  });

  it('reports determinate upload progress before the normalized Hub result', () => {
    const service = TestBed.inject(ModelTrainingApiService);
    const events: any[] = [];

    service.uploadDatasetWithProgress('http://hub.test', {
      file: new File(['{}'], 'training.json'), purpose: 'test', license: 'private', privacy: 'private',
      validation_ratio: 0.2, split_seed: 42,
    }, 'progress-key').subscribe(event => events.push(event));

    expect(events).toEqual([
      { kind: 'progress', loaded: 50, total: 100, percent: 50 },
      { kind: 'complete', dataset: { id: 'dataset-1' } },
    ]);
    expect(core.requestEvents).toHaveBeenCalledWith(
      'POST', 'http://hub.test/api/ml-intern-training/datasets', 'http://hub.test',
      expect.objectContaining({ headers: { 'Idempotency-Key': 'progress-key' }, timeoutMs: 120_000 }),
    );
  });

  it('creates and cancels jobs with explicit, idempotent Hub mutations', () => {
    const service = TestBed.inject(ModelTrainingApiService);
    const payload = trainingRequest();

    service.createJob('http://hub.test', payload, 'create-key').subscribe();
    service.cancelJob('http://hub.test', 'job-1', ' operator request ', 'cancel-key').subscribe();

    expect(core.request.mock.calls[0]).toEqual([
      'POST',
      'http://hub.test/api/ml-intern-training/jobs',
      'http://hub.test',
      expect.objectContaining({ body: payload, headers: { 'Idempotency-Key': 'create-key' } }),
    ]);
    expect(core.request.mock.calls[1]).toEqual([
      'POST',
      'http://hub.test/api/ml-intern-training/jobs/job-1/cancel',
      'http://hub.test',
      { body: { reason: 'operator request' }, headers: { 'Idempotency-Key': 'cancel-key' } },
    ]);
  });

  it('streams events with the Hub bearer and parses bounded SSE frames', async () => {
    const encoder = new TextEncoder();
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => new Response(
      new ReadableStream({
        start(controller) {
          controller.enqueue(encoder.encode(': heartbeat\n\ndata: {"sequence":7,"event_type":"progress"}\n\n'));
          controller.close();
        },
      }),
      { status: 200, headers: { 'Content-Type': 'text/event-stream' } },
    ));
    vi.stubGlobal('fetch', fetchMock);
    const service = TestBed.inject(ModelTrainingApiService);
    const events: unknown[] = [];

    await new Promise<void>((resolve, reject) => {
      service.streamJobEvents('http://hub.test', 'job/1', 6).subscribe({
        next: value => events.push(value),
        error: reject,
        complete: resolve,
      });
    });

    expect(events).toEqual([{ sequence: 7, event_type: 'progress' }]);
    expect(fetchMock).toHaveBeenCalledWith(
      'http://hub.test/api/ml-intern-training/jobs/job%2F1/events?after_sequence=6&limit=200&stream=true',
      expect.objectContaining({
        method: 'GET',
        headers: { Accept: 'text/event-stream', Authorization: 'Bearer hub-user-token' },
      }),
    );
    vi.unstubAllGlobals();
  });

  it('attaches external validation and deletes datasets with exact idempotent Hub contracts', () => {
    const service = TestBed.inject(ModelTrainingApiService);

    service.attachValidationDataset(
      'http://hub.test', 'train/1', { validation_dataset_id: 'validation-2' }, 'attach-key',
    ).subscribe();
    service.deleteDataset('http://hub.test', 'validation/2', 'delete-key').subscribe();

    expect(core.request.mock.calls[0]).toEqual([
      'POST',
      'http://hub.test/api/ml-intern-training/datasets/train%2F1/validation-dataset',
      'http://hub.test',
      {
        body: { validation_dataset_id: 'validation-2' },
        headers: { 'Idempotency-Key': 'attach-key' },
        timeoutMs: 120_000,
      },
    ]);
    expect(core.request.mock.calls[1]).toEqual([
      'DELETE',
      'http://hub.test/api/ml-intern-training/datasets/validation%2F2',
      'http://hub.test',
      { headers: { 'Idempotency-Key': 'delete-key' } },
    ]);
    expect(core.request.mock.calls.flatMap(call => Object.keys(call[3]?.body || {}))).not.toContain('force');
  });

  it('keeps adapter import, evaluation, approval and export behind the Hub API', () => {
    const service = TestBed.inject(ModelTrainingApiService);
    const bundle = new File(['safe'], 'adapter.zip', { type: 'application/zip' });

    service.importAdapter('http://hub.test', {
      name: ' Local adapter ', base_model_id: ' model-1 ', method: 'qlora', bundle,
    }, 'import-key').subscribe();
    service.evaluateAdapter(
      'http://hub.test', 'adapter-1', 'dataset-1', 'ananta_todo_json', false, '', 'eval-key',
    ).subscribe();
    service.decideAdapter('http://hub.test', 'adapter-1', 'approve', {
      reason: 'Evaluation passed', expected_version: 3, confirmed: true,
    }, 'approve-key').subscribe();
    service.exportAdapter('http://hub.test', 'adapter-1', 'export-key').subscribe();

    expect(core.request.mock.calls.map(call => call[1])).toEqual([
      'http://hub.test/api/ml-intern-training/adapters/import',
      'http://hub.test/api/ml-intern-training/evaluations',
      'http://hub.test/api/ml-intern-training/adapters/adapter-1/approve',
      'http://hub.test/api/ml-intern-training/adapters/adapter-1/export',
    ]);
    expect(core.request.mock.calls.map(call => call[3].headers['Idempotency-Key'])).toEqual([
      'import-key', 'eval-key', 'approve-key', 'export-key',
    ]);
    expect(core.request.mock.calls[0][3].body.get('bundle')).toMatchObject({ name: bundle.name, size: bundle.size });
    expect(core.request.mock.calls[1][3].body).toEqual({
      adapter_id: 'adapter-1', dataset_id: 'dataset-1', scorer_name: 'ananta_todo_json',
    });
  });

  it('downloads export bytes through the authenticated Hub transport', () => {
    const service = TestBed.inject(ModelTrainingApiService);
    const downloads: any[] = [];

    service.downloadAdapterExport('http://hub.test/', 'lora-export-a/b').subscribe(value => downloads.push(value));

    expect(core.requestBlob).toHaveBeenCalledWith(
      'http://hub.test/api/ml-intern-training/exports/lora-export-a%2Fb',
      'http://hub.test/',
      120_000,
    );
    expect(downloads).toEqual([expect.objectContaining({
      filename: 'lora-export-a_b.zip',
      sha256: 'abc123',
    })]);
    expect(downloads[0].blob).toBeInstanceOf(Blob);
  });

  it('keeps runtime unload and rollback separate from Registry routes with exact confirmation bodies', () => {
    const service = TestBed.inject(ModelTrainingApiService);
    const payload = { confirmed: true as const, reason: 'operator confirmed runtime action' };

    service.unloadRuntimeAdapter('http://hub.test/', 'adapter/1', payload).subscribe();
    service.rollbackRuntimeAdapter('http://hub.test', 'adapter/1', payload).subscribe();

    expect(core.request.mock.calls).toEqual([
      [
        'POST',
        'http://hub.test/api/ml-intern-lora-runtime/adapters/adapter%2F1/unload',
        'http://hub.test/',
        { body: payload },
      ],
      [
        'POST',
        'http://hub.test/api/ml-intern-lora-runtime/adapters/adapter%2F1/rollback',
        'http://hub.test',
        { body: payload },
      ],
    ]);
    expect(core.request.mock.calls.map(call => call[1])).not.toContain(
      'http://hub.test/api/ml-intern-training/adapters/adapter%2F1/rollback',
    );
  });
});

function trainingRequest(): CreateTrainingJobRequest {
  return {
    dataset_id: 'dataset-1', base_model_id: 'model-1', backend: 'peft', mode: 'dry_run',
    gpu_profile: 'gpu-1', method: 'qlora', output_name: 'adapter-1',
    hyperparameters: {
      lora_rank: 16, lora_alpha: 32, lora_dropout: 0.05, learning_rate: 0.0002,
      batch_size: 1, gradient_accumulation_steps: 8, max_steps: 100,
      max_sequence_length: 2048, quantization: '4bit',
    },
    require_dataset_validation: true,
    require_secret_scan: true,
  };
}
