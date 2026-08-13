import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { firstValueFrom } from 'rxjs';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { AgentDirectoryService } from '../../../services/agent-directory.service';
import { SUPPRESS_GLOBAL_ERROR_NOTIFICATION } from '../../../services/error-request-context';
import { CaseFlowEdgeTraceApiService } from './caseflow-edge-trace-api.service';

describe('CaseFlowEdgeTraceApiService', () => {
  let api: CaseFlowEdgeTraceApiService;
  let http: HttpTestingController;

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        CaseFlowEdgeTraceApiService,
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ role: 'hub', url: 'https://hub.example/' }] },
        },
      ],
    });
    api = TestBed.inject(CaseFlowEdgeTraceApiService);
    http = TestBed.inject(HttpTestingController);
  });

  afterEach(() => http.verify());

  it('posts a versioned run-bound query to the encoded Hub read endpoint', async () => {
    const promise = firstValueFrom(api.read({ workflow_id: 'workflow?a', run_id: 'run-a' }));
    const request = http.expectOne(
      'https://hub.example/api/visual-process/workflow/workflow%3Fa/caseflow-edge-trace',
    );

    expect(request.request.method).toBe('POST');
    expect(request.request.params.keys()).toEqual([]);
    expect(request.request.body).toEqual({
      schema: 'ananta.caseflow_edge_trace_query.v1',
      run_id: 'run-a',
    });
    expect(request.request.context.get(SUPPRESS_GLOBAL_ERROR_NOTIFICATION)).toBe(true);
    request.flush(response('workflow?a', 'run-a'));

    await expect(promise).resolves.toMatchObject({ workflow_id: 'workflow?a', run_id: 'run-a' });
  });

  it('fails before transport for a non-canonical scope or Hub origin', async () => {
    await expect(firstValueFrom(api.read({ workflow_id: ' workflow-a', run_id: 'run-a' })))
      .rejects.toThrow('caseflow_workflow_id_invalid');
    http.expectNone(() => true);

    TestBed.resetTestingModule();
    TestBed.configureTestingModule({
      providers: [
        CaseFlowEdgeTraceApiService,
        provideHttpClient(),
        provideHttpClientTesting(),
        {
          provide: AgentDirectoryService,
          useValue: { list: () => [{ role: 'hub', url: 'javascript:unsafe' }] },
        },
      ],
    });
    const invalidOriginApi = TestBed.inject(CaseFlowEdgeTraceApiService);
    const invalidOriginHttp = TestBed.inject(HttpTestingController);
    await expect(firstValueFrom(invalidOriginApi.read({ workflow_id: 'workflow-a', run_id: 'run-a' })))
      .rejects.toThrow('caseflow_hub_origin_unavailable');
    invalidOriginHttp.expectNone(() => true);
    invalidOriginHttp.verify();
  });

  it('rejects a validly shaped response bound to another run', async () => {
    const promise = firstValueFrom(api.read({ workflow_id: 'workflow-a', run_id: 'run-a' }));
    const request = http.expectOne(
      'https://hub.example/api/visual-process/workflow/workflow-a/caseflow-edge-trace',
    );
    request.flush(response('workflow-a', 'run-other'));

    await expect(promise).rejects.toThrow('caseflow_edge_trace_scope_mismatch');
  });
});

function response(workflowId: string, runId: string): Record<string, unknown> {
  return {
    schema: 'ananta.caseflow_edge_trace_read_model.v1',
    workflow_id: workflowId,
    run_id: runId,
    catalog_verification_status: 'verified',
    verification_status: 'verified',
    reason_code: '',
    edges: [],
    telemetry: {
      source_event_count: 0,
      processed_event_count: 0,
      rejected_event_count: 0,
      truncated_event_count: 0,
      correlated_edge_count: 0,
      redaction_policy: 'user',
      messages_per_edge_limit: 64,
      telemetry_per_edge_limit: 128,
    },
  };
}
