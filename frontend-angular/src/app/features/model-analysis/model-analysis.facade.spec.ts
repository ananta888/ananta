import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { AgentDirectoryService } from '../../services/agent-directory.service';
import { ModelAnalysisApiClient } from './model-analysis-api.client';
import { ModelAnalysisFacade } from './model-analysis.facade';

describe('ModelAnalysisFacade', () => {
  const directory = {
    list: vi.fn(() => [{ name: 'hub', role: 'hub', url: 'http://hub.test/' }]),
  };
  const api = {
    capabilities: vi.fn(() => of({
      supported: true,
      max_graph_nodes: 200,
      max_graph_edges: 400,
    })),
    listJobs: vi.fn(() => of({ items: [], next_cursor: null })),
    startJob: vi.fn(() => of(job('job-1', 'completed'))),
    getJob: vi.fn(() => of(job('job-1', 'completed'))),
    cancelJob: vi.fn(() => of(job('job-1', 'cancel_requested'))),
    getReport: vi.fn(() => of({
      schema: 'report.v1',
      content_digest: 'sha256:abc',
      sections: [{ name: 'trace', status: 'unsupported', reason_code: 'no_trace' }],
    })),
    getGraph: vi.fn(() => of({
      schema: 'graph.v1',
      nodes: [{ node_id: 'n1', label: 'Model', kind: 'model' }],
      edges: [],
      truncated: false,
    })),
  };

  beforeEach(() => {
    vi.clearAllMocks();
    api.capabilities.mockReturnValue(of({
      supported: true,
      max_graph_nodes: 200,
      max_graph_edges: 400,
    }));
    api.listJobs.mockReturnValue(of({ items: [], next_cursor: null }));
    TestBed.configureTestingModule({
      providers: [
        ModelAnalysisFacade,
        { provide: ModelAnalysisApiClient, useValue: api },
        { provide: AgentDirectoryService, useValue: directory },
      ],
    });
  });

  it('distinguishes empty and unsupported states', () => {
    const facade = TestBed.inject(ModelAnalysisFacade);
    facade.loadOverview();
    expect(facade.viewState()).toBe('empty');
    expect(facade.stateReasonCode()).toBe('model_analysis_jobs_empty');

    api.capabilities.mockReturnValue(of({
      supported: false,
      reason_code: 'runtime_unavailable',
      max_graph_nodes: 200,
      max_graph_edges: 400,
    }));
    facade.loadOverview();
    expect(facade.viewState()).toBe('unsupported');
    expect(facade.stateReasonCode()).toBe('runtime_unavailable');
  });

  it('distinguishes permission failures from generic errors', () => {
    api.capabilities.mockReturnValue(throwError(() => ({ status: 403 })));
    const denied = TestBed.inject(ModelAnalysisFacade);
    denied.loadOverview();
    expect(denied.viewState()).toBe('permission');
    expect(denied.stateReasonCode()).toBe('model_analysis_permission_denied');

    api.capabilities.mockReturnValue(throwError(() => ({
      status: 503,
      error: { message: 'temporarily unavailable' },
    })));
    denied.loadOverview();
    expect(denied.viewState()).toBe('error');
    expect(denied.stateReasonCode()).toBe('model_analysis_request_failed');
    expect(denied.error()).toContain('temporarily unavailable');
  });

  it('runs import-ref to report only through the API client', () => {
    const facade = TestBed.inject(ModelAnalysisFacade);
    facade.loadOverview();
    facade.start('import:model-1');

    expect(api.startJob).toHaveBeenCalledWith(
      'http://hub.test',
      expect.objectContaining({
        import_ref: 'import:model-1',
        requested_artifact_kinds: ['report', 'model_graph'],
      }),
      expect.stringMatching(/^model-analysis-start-/),
    );
    expect(api.getJob).toHaveBeenCalledWith('http://hub.test', 'job-1');
    expect(api.getReport).toHaveBeenCalledWith('http://hub.test', 'job-1');
    expect(api.getGraph).toHaveBeenCalledWith('http://hub.test', 'job-1');
    expect(facade.report()?.sections[0].status).toBe('unsupported');
    expect(facade.graph()?.nodes[0].node_id).toBe('n1');
  });
});

function job(jobId: string, status: string): any {
  return {
    job_id: jobId,
    model_id: 'model-1',
    analysis_kind: 'full',
    profile_id: 'bounded-ui',
    requested_artifact_kinds: ['report', 'model_graph'],
    status,
    progress_percent: status === 'completed' ? 100 : 10,
  };
}
