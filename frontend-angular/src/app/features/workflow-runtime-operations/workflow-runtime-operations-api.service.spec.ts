import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiCoreService } from '../../services/hub-api-core.service';
import { WorkflowRuntimeOperationsApiService } from './workflow-runtime-operations-api.service';

describe('WorkflowRuntimeOperationsApiService', () => {
  const core = {
    get: vi.fn(() => of({ runs: [] })),
    request: vi.fn(() => of({ status: 'ok', command: {} })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    TestBed.configureTestingModule({
      providers: [
        WorkflowRuntimeOperationsApiService,
        { provide: HubApiCoreService, useValue: core },
      ],
    });
  });

  it('reads filtered operations only through the Hub endpoint', () => {
    const service = TestBed.inject(WorkflowRuntimeOperationsApiService);
    service.list('http://hub.test', {
      runtime: 'temporal', mode: 'durable', status: '', health: 'degraded', q: 'run-7',
    }).subscribe();

    const url = String(core.get.mock.calls[0][0]);
    expect(url).toContain('http://hub.test/api/workflow-runtime/operations?');
    expect(url).toContain('runtime=temporal');
    expect(url).toContain('mode=durable');
    expect(url).toContain('health=degraded');
    expect(url).not.toContain('worker');
    expect(url).not.toContain('temporal:');
  });

  it('reads the shared capability projection from the authenticated Hub endpoint', () => {
    const service = TestBed.inject(WorkflowRuntimeOperationsApiService);

    service.capabilities('http://hub.test', ['resume', 'durability', 'resume']).subscribe();

    expect(core.get).toHaveBeenCalledWith(
      'http://hub.test/api/workflow-runtime/capabilities?required_capability=durability&required_capability=resume',
      'http://hub.test',
      undefined,
      false,
    );
  });

  it('posts commands to the Hub facade with an idempotency header', () => {
    const service = TestBed.inject(WorkflowRuntimeOperationsApiService);
    service.command(
      'http://hub.test',
      'run/7',
      { type: 'cancel_run', approval_id: 'approval-1', evidence_refs: ['ev-1'] },
      'command-idempotency-1',
    ).subscribe();

    expect(core.request).toHaveBeenCalledWith(
      'POST',
      'http://hub.test/api/workflow-runtime/operations/runs/run%2F7/commands',
      'http://hub.test',
      expect.objectContaining({
        headers: { 'Idempotency-Key': 'command-idempotency-1' },
      }),
    );
  });
});
