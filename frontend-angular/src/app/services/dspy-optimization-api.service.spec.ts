import { TestBed } from '@angular/core/testing';
import { firstValueFrom, of } from 'rxjs';

import { DspyOptimizationApiService } from './dspy-optimization-api.service';
import { HubApiCoreService } from './hub-api-core.service';
import { SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT } from './sfu-secure-endpoint.policy';

describe('DspyOptimizationApiService', () => {
  const core = { request: vi.fn() };

  beforeEach(() => {
    core.request.mockReset();
    TestBed.configureTestingModule({ providers: [
      DspyOptimizationApiService,
      { provide: HubApiCoreService, useValue: core },
      { provide: SFU_ALLOW_INSECURE_LOCALHOST_TRANSPORT, useValue: false },
    ] });
  });

  afterEach(() => TestBed.resetTestingModule());

  it('parses the shared Hub capability envelope without a model call', async () => {
    core.request.mockReturnValue(of({ status: 'success', data: {
      state: 'disabled', reason_code: 'dspy_optimization_disabled', mode: 'disabled',
      installed_version: null, optimizer_capabilities: ['labeled_few_shot'],
      program_kinds: ['planning_structured_tasks'], limits: { max_model_calls: 10 },
      human_intervention_required: false,
    } }));
    const result = await firstValueFrom(TestBed.inject(DspyOptimizationApiService).capabilities('https://hub.example'));
    expect(result.state).toBe('disabled');
    expect(result.humanInterventionRequired).toBe(false);
  });

  it('rejects insecure non-local Hub endpoints before a request', () => {
    expect(() => TestBed.inject(DspyOptimizationApiService).runs('http://remote.example', 'tenant-1'))
      .toThrow('dspy_hub_endpoint_denied');
    expect(core.request).not.toHaveBeenCalled();
  });
});
