import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import { ModelAnalysisApiClient } from './model-analysis-api.client';

describe('ModelAnalysisApiClient', () => {
  const core = {
    get: vi.fn(() => of({})),
    request: vi.fn(() => of({})),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        ModelAnalysisApiClient,
        { provide: HubApiCoreService, useValue: core },
      ],
    });
  });

  it('uses only bounded Hub model-intelligence endpoints', () => {
    const client = TestBed.inject(ModelAnalysisApiClient);
    client.capabilities('http://hub.test/').subscribe();
    client.listJobs('http://hub.test', 'cursor/1', 900).subscribe();
    client.getJob('http://hub.test', 'job/1').subscribe();
    client.getReport('http://hub.test', 'job/1').subscribe();
    client.getGraph('http://hub.test', 'job/1').subscribe();

    expect(core.get.mock.calls.map(call => call[0])).toEqual([
      'http://hub.test/api/model-intelligence/capabilities',
      'http://hub.test/api/model-intelligence/jobs?page_size=100&cursor=cursor%2F1',
      'http://hub.test/api/model-intelligence/jobs/job%2F1',
      'http://hub.test/api/model-intelligence/jobs/job%2F1/report',
      'http://hub.test/api/model-intelligence/jobs/job%2F1/graph?max_nodes=200&max_edges=400',
    ]);
    expect(String(core.get.mock.calls)).not.toContain('/artifacts/');
    expect(String(core.get.mock.calls)).not.toContain('codecompass');
  });

  it('starts and cancels jobs with explicit idempotency', () => {
    const client = TestBed.inject(ModelAnalysisApiClient);
    const request = {
      import_ref: 'import:model-1',
      analysis_kind: 'full' as const,
      profile_id: 'bounded-ui' as const,
      requested_artifact_kinds: ['report', 'model_graph'] as const,
    };
    client.startJob('http://hub.test', request, 'start-key').subscribe();
    client.cancelJob('http://hub.test', 'job/1', 'cancel-key').subscribe();

    expect(core.request.mock.calls).toEqual([
      [
        'POST',
        'http://hub.test/api/model-intelligence/jobs',
        'http://hub.test',
        {
          body: request,
          headers: { 'Idempotency-Key': 'start-key' },
          timeoutMs: 30_000,
        },
      ],
      [
        'POST',
        'http://hub.test/api/model-intelligence/jobs/job%2F1/cancel',
        'http://hub.test',
        {
          body: { reason: 'operator_requested' },
          headers: { 'Idempotency-Key': 'cancel-key' },
        },
      ],
    ]);
  });
});
