import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';

import { DspyOptimizationApiService } from './dspy-optimization-api.service';
import { HubApiCoreService } from './hub-api-core.service';

describe('DspyOptimizationApiService', () => {
  const core = { request: vi.fn() };

  beforeEach(() => {
    core.request.mockReset();
    TestBed.configureTestingModule({ providers: [
      DspyOptimizationApiService,
      { provide: HubApiCoreService, useValue: core },
    ] });
  });

  afterEach(() => TestBed.resetTestingModule());

  it('parses the shared Hub capability envelope without a model call', async () => {
    core.request.mockReturnValue(of({
      state: 'disabled', reason_code: 'dspy_optimization_disabled', mode: 'disabled',
      installed_version: null, optimizer_capabilities: ['labeled_few_shot'],
      program_kinds: ['planning_structured_tasks'], limits: { max_model_calls: 10 },
      provider_profiles: ['local.default'], metric_sets: ['deterministic-v1'], policy_digest: 'a'.repeat(64),
      human_intervention_required: false,
    }));
    const result = await firstValueFrom(TestBed.inject(DspyOptimizationApiService).capabilities('https://hub.example'));
    expect(result.state).toBe('disabled');
    expect(result.humanInterventionRequired).toBe(false);
    expect(result.providerProfiles).toEqual(['local.default']);
  });

  it('rejects non-origin Hub endpoints before a request', () => {
    expect(() => TestBed.inject(DspyOptimizationApiService).runs('https://user@hub.example/api', 'tenant-1'))
      .toThrow('dspy_hub_endpoint_denied');
    expect(core.request).not.toHaveBeenCalled();
  });

  it('loads the canonical tenant and scope provenance projection', async () => {
    core.request.mockReturnValue(of({
      schema: 'ananta.dspy-promotion-provenance.v1', current_revision: 2, history: [],
    }));
    const result = await firstValueFrom(
      TestBed.inject(DspyOptimizationApiService).provenance('https://hub.example', 'tenant-1', 'planning-en'),
    );
    expect(result['current_revision']).toBe(2);
    expect(core.request).toHaveBeenCalledWith(
      'GET',
      'https://hub.example/api/dspy-optimization/provenance?tenant_id=tenant-1&scope_id=planning-en',
      'https://hub.example',
      { timeoutMs: 8_000 },
    );
  });
});
