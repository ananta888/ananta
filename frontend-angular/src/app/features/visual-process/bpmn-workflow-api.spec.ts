// Synthetic HTTP contract checks; no live authentication or production evidence.
import { provideHttpClient } from '@angular/common/http';
import { HttpTestingController, provideHttpClientTesting } from '@angular/common/http/testing';
import { TestBed } from '@angular/core/testing';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { AgentDirectoryService } from '../../services/agent-directory.service';
import { VisualProcessApiService, VpGraph } from './visual-process-api.service';

describe('BPMN Hub HTTP contracts', () => {
  let api: VisualProcessApiService;
  let http: HttpTestingController;
  const graph: VpGraph = { id: 'synthetic', name: 'Test', description: '', version: '1', steps: [], edges: [], tags: [] };
  const binding = { expected_revision: 3, run_id: 'synthetic-run', plan_hash: 'synthetic-plan', checkpoint_ref: 'synthetic-checkpoint' };

  beforeEach(() => {
    TestBed.configureTestingModule({ providers: [VisualProcessApiService, provideHttpClient(), provideHttpClientTesting(),
      { provide: AgentDirectoryService, useValue: { list: () => [{ role: 'hub', url: 'http://hub' }] } },
    ] });
    api = TestBed.inject(VisualProcessApiService);
    http = TestBed.inject(HttpTestingController);
  });
  afterEach(() => { http.verify(); TestBed.resetTestingModule(); });

  it('uses the start graph/options body for preflight', () => {
    const options = { policy_scope: { source: 'bpmn_blueprint_editor' } };
    api.preflightWorkflowFromGraph(graph, options).subscribe();
    const request = http.expectOne('http://hub/api/visual-process/workflow/preflight');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ graph, ...options });
    request.flush({ ready: false, reason_codes: ['synthetic-denial'] });
  });

  it.each(['resume', 'retry', 'cancel'] as const)('posts %s with exact loaded preconditions to the existing Hub route', command => {
    api.controlBpmnWorkflow('workflow / test', command, binding, 'synthetic-command').subscribe();
    const request = http.expectOne(`http://hub/api/visual-process/workflow/workflow%20%2F%20test/${command}`);
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual(command === 'cancel'
      ? { ...binding, reason: 'BPMN editor cancellation', command_id: 'synthetic-command' }
      : { expected_revision: binding.expected_revision, plan_hash: binding.plan_hash,
        payload: { run_id: binding.run_id, checkpoint_ref: binding.checkpoint_ref }, command_id: 'synthetic-command' });
    request.flush({});
  });

  it('keeps edge trace run identity in the POST body', () => {
    api.getBpmnEdgeTrace('workflow', binding.run_id).subscribe();
    const request = http.expectOne('http://hub/api/visual-process/workflow/workflow/caseflow-edge-trace');
    expect(request.request.method).toBe('POST');
    expect(request.request.body).toEqual({ schema: 'ananta.caseflow_edge_trace_query.v1', run_id: binding.run_id });
    request.flush({ edges: [] });
  });
});
