import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';

import { HubApiCoreService } from './hub-api-core.service';
import {
  SpeechReconciliationApiContractError,
  SpeechReconciliationApiService,
  SpeechReconciliationCreateRequest,
  SpeechResourceVectorView,
  parseSpeechReconciliationJob,
} from './speech-reconciliation-api.service';

const vector = (value: number): SpeechResourceVectorView => ({
  wall_time_ms: value,
  cpu_time_ms: value,
  gpu_time_ms: value,
  memory_byte_ms: value,
  disk_bytes: value,
  checkpoint_bytes: value,
  energy_millijoules: value,
});

const job = (overrides: Record<string, unknown> = {}) => ({
  job_id: 'speech-reconciliation-job-1',
  state: 'running',
  stage: 'alignment',
  reason_code: 'speech_reconciliation_running',
  source_duration_ms: 90_000,
  max_compute_factor: 10,
  ledger_sequence: 3,
  key_epoch: 2,
  checkpoint_count: 4,
  conflict_counts: { resolved: 8, unresolved: 2, rejected: 1, quarantined: 3 },
  budget: {
    allocated: vector(10),
    reserved: vector(2),
    consumed: vector(3),
    remaining: vector(5),
  },
  active_attempt_id: 'speech-reconciliation-attempt-1',
  version: 7,
  created_at_ms: 1_700_000_000_000,
  updated_at_ms: 1_700_000_001_000,
  finished_at_ms: null,
  ...overrides,
});

describe('SpeechReconciliationApiService', () => {
  const core = {
    get: vi.fn(),
    request: vi.fn(),
  };
  let service: SpeechReconciliationApiService;

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        SpeechReconciliationApiService,
        { provide: HubApiCoreService, useValue: core },
      ],
    });
    service = TestBed.inject(SpeechReconciliationApiService);
  });

  it('reads a bounded, closed page from the additive Hub endpoint', async () => {
    core.get.mockReturnValue(of({ ok: true, data: { jobs: [job()], next_offset: 25 } }));

    const page = await firstValueFrom(service.list('http://hub:5000/', 0, 25));

    expect(page.jobs[0]).toMatchObject({ state: 'running', stage: 'alignment', checkpoint_count: 4 });
    expect(core.get).toHaveBeenCalledWith(
      'http://hub:5000/v1/voice/speech-reconciliation?offset=0&limit=25',
      'http://hub:5000/',
      undefined,
      false,
    );
  });

  it('sends matching idempotency and revision preconditions for every mutation', async () => {
    core.request.mockReturnValue(of({ ok: true, data: { job: job({ version: 8, max_compute_factor: 5 }) } }));

    await firstValueFrom(service.reduce(
      'http://hub:5000',
      'speech-reconciliation-job-1',
      7,
      5,
      'reconciliation-reduce-7',
    ));

    expect(core.request).toHaveBeenCalledWith(
      'POST',
      'http://hub:5000/v1/voice/speech-reconciliation/speech-reconciliation-job-1/reduce',
      'http://hub:5000',
      {
        body: { expected_version: 7, max_compute_factor: 5 },
        headers: { 'Idempotency-Key': 'reconciliation-reduce-7', 'If-Match': '"7"' },
      },
    );
  });

  it('validates the closed create DTO and parses its bounded budget plan', async () => {
    const request: SpeechReconciliationCreateRequest = {
      consent_id: 'consent-1',
      consent_version: 2,
      revocation_epoch: 0,
      input_manifest_digest: 'a'.repeat(64),
      policy_digest: 'b'.repeat(64),
      research_policy_ref: null,
      max_compute_factor: 10,
      key_epoch: 3,
      deadline_at_ms: Date.now() + 120_000,
      resource_limits: vector(100),
    };
    core.request.mockReturnValue(of({
      ok: true,
      data: {
        job: {
          ...job(),
          budget_plan: { compute_factor: 10, compute_equivalent_ms: 900_000, allocated: vector(100) },
        },
      },
    }));

    const accepted = await firstValueFrom(service.create('http://hub:5000', request, 'reconciliation-create-1'));

    expect(accepted.budget_plan).toMatchObject({ compute_factor: 10, compute_equivalent_ms: 900_000 });
    expect(core.request.mock.calls[0][3]).toEqual({
      body: request,
      headers: { 'Idempotency-Key': 'reconciliation-create-1' },
    });
  });

  it('fails closed on content fields, unknown fields, unsafe numbers and broken budget arithmetic', () => {
    expect(() => parseSpeechReconciliationJob({ ...job(), transcript: 'must not cross this boundary' }))
      .toThrow(SpeechReconciliationApiContractError);
    expect(() => parseSpeechReconciliationJob(job({ source_duration_ms: Number.MAX_SAFE_INTEGER })))
      .toThrow('speech_reconciliation_source_duration_ms_invalid');
    expect(() => parseSpeechReconciliationJob(job({
      budget: {
        allocated: vector(11), reserved: vector(2), consumed: vector(3), remaining: vector(5),
      },
    }))).toThrow('speech_reconciliation_budget_arithmetic_invalid');
  });

  it('rejects unbounded pagination and an empty resource cap before issuing a request', () => {
    expect(() => service.list('http://hub:5000', 0, 101)).toThrow('speech_reconciliation_limit_invalid');
    expect(() => service.create('http://hub:5000', {
      consent_id: 'consent-1', consent_version: 1, revocation_epoch: 0,
      input_manifest_digest: 'a'.repeat(64), policy_digest: 'b'.repeat(64), research_policy_ref: null,
      max_compute_factor: 1, key_epoch: 1, deadline_at_ms: Date.now() + 120_000,
      resource_limits: vector(0),
    }, 'reconciliation-create-2')).toThrow('speech_reconciliation_resource_limits_empty');
    expect(core.request).not.toHaveBeenCalled();
  });
});
