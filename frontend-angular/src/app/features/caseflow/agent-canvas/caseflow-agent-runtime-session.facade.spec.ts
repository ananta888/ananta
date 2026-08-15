import { HttpErrorResponse } from '@angular/common/http';
import { TestBed } from '@angular/core/testing';
import { Subject } from 'rxjs';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { VisualProcessApiService } from '../../visual-process/visual-process-api.service';
import {
  CASEFLOW_AGENT_RUNTIME_SESSION_CONFIG,
  CASEFLOW_EDGE_TRACE_READER,
  CaseFlowAgentRuntimeSessionFacade,
} from './caseflow-agent-runtime-session.facade';

describe('CaseFlowAgentRuntimeSessionFacade', () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it('serializes refreshes and reads trace only after a canonical top-level run ID', () => {
    const { facade, api, traceReader, statusResponses, traceResponses } = setup();

    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });
    facade.refresh();
    facade.refresh();
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(1);
    expect(traceReader.read).not.toHaveBeenCalled();

    statusResponses[0].next(runtimeStatus({ revision: 1 }));
    expect(traceReader.read).toHaveBeenCalledWith({ workflow_id: 'graph-a', run_id: 'run-a' });
    facade.refresh();
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(1);

    traceResponses[0].next(traceReadModel());
    traceResponses[0].complete();
    facade.refresh();

    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);
    expect(facade.state()).toBe('active');
    expect(facade.runId()).toBe('run-a');
    expect(facade.revision()).toBe(1);
    expect(facade.runtimeOverlay()?.workflow_id).toBe('graph-a');
    expect(facade.edgeTraceReadModel()?.run_id).toBe('run-a');
  });

  it('does not query trace or retain evidence for a missing or invalid run identity', () => {
    const { facade, traceReader, statusResponses } = setup();
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });

    statusResponses[0].next({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: 'graph-a', revision: 1, updated_at: 1, status: 'running', steps: [],
    });

    expect(traceReader.read).not.toHaveBeenCalled();
    expect(facade.runtimeOverlay()).toBeNull();
    expect(facade.edgeTraceReadModel()).toBeNull();
    expect(facade.state()).toBe('error');
    expect(facade.errorCode()).toBe('vp_runtime_run_id_invalid');
    expect(facade.canRefresh()).toBe(false);
  });

  it('keeps polling an initial 404 only up to the configured no-run bound', () => {
    vi.useFakeTimers();
    const { facade, api, statusResponses, traceResponses } = setup({
      maxNotFoundPolls: 2,
      intervalMs: 50,
    });
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });

    statusResponses[0].error(httpError(404));
    expect(facade.state()).toBe('no_run');
    expect(facade.runtimeOverlay()).toBeNull();

    vi.advanceTimersByTime(50);
    statusResponses[1].error(httpError(404));
    expect(facade.state()).toBe('no_run_timeout');
    expect(facade.errorCode()).toBe('caseflow_runtime_no_run_timeout');
    expect(facade.canRefresh()).toBe(true);

    vi.advanceTimersByTime(500);
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);

    facade.refresh();
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(3);
    statusResponses[2].next(runtimeStatus({ revision: 1 }));
    traceResponses[0].next(traceReadModel());
    traceResponses[0].complete();
    expect(facade.state()).toBe('active');
  });

  it('treats a canonical not_found envelope as bounded no-run without tracing', () => {
    const { facade, traceReader, statusResponses } = setup();
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });

    statusResponses[0].next({
      schema: 'ananta.workflow_backend_status.v1',
      workflow_id: 'graph-a',
      status: 'not_found',
      steps: [],
    });

    expect(facade.state()).toBe('no_run');
    expect(traceReader.read).not.toHaveBeenCalled();
  });

  it.each([401, 403, 404])(
    'synchronously clears runtime and trace when HTTP %s follows evidence',
    statusCode => {
      const { facade, statusResponses, traceResponses } = setup();
      facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });
      completeCycle(statusResponses[0], traceResponses, runtimeStatus({ revision: 1 }));
      expect(facade.runtimeOverlay()).not.toBeNull();
      expect(facade.edgeTraceReadModel()).not.toBeNull();

      facade.refresh();
      statusResponses[1].error(httpError(statusCode));

      expect(facade.runtimeOverlay()).toBeNull();
      expect(facade.edgeTraceReadModel()).toBeNull();
      expect(facade.runId()).toBeNull();
      expect(facade.revision()).toBeNull();
      expect(facade.state()).toBe('access_revoked');
      expect(facade.errorCode()).toBe(`caseflow_runtime_http_${statusCode}`);
    },
  );

  it('clears both projections when trace access disappears after runtime evidence', () => {
    const { facade, statusResponses, traceResponses } = setup();
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });
    completeCycle(statusResponses[0], traceResponses, runtimeStatus({ revision: 1 }));

    facade.refresh();
    statusResponses[1].next(runtimeStatus({ revision: 2 }));
    traceResponses[1].error(httpError(404));

    expect(facade.runtimeOverlay()).toBeNull();
    expect(facade.edgeTraceReadModel()).toBeNull();
    expect(facade.state()).toBe('access_revoked');
  });

  it('keeps the new runtime but clears stale trace on a transient trace failure', () => {
    const { facade, statusResponses, traceResponses } = setup();
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });
    completeCycle(statusResponses[0], traceResponses, runtimeStatus({ revision: 1 }));

    facade.refresh();
    statusResponses[1].next(runtimeStatus({ revision: 2, status: 'running' }));
    traceResponses[1].error(httpError(503));

    expect(facade.revision()).toBe(2);
    expect(facade.runtimeOverlay()).not.toBeNull();
    expect(facade.edgeTraceReadModel()).toBeNull();
    expect(facade.state()).toBe('active');
    expect(facade.errorCode()).toBe('caseflow_runtime_trace_unavailable');
  });

  it('ignores an older revision before making a trace request', () => {
    const { facade, traceReader, statusResponses, traceResponses } = setup();
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });
    completeCycle(statusResponses[0], traceResponses, runtimeStatus({ revision: 2 }));
    expect(traceReader.read).toHaveBeenCalledTimes(1);

    facade.refresh();
    statusResponses[1].next(runtimeStatus({ revision: 1, status: 'failed' }));

    expect(traceReader.read).toHaveBeenCalledTimes(1);
    expect(facade.revision()).toBe(2);
    expect(facade.runtimeOverlay()?.overall_status).toBe('running');
  });

  it('cancels stale generations and accepts only the newly attached graph', () => {
    const { facade, statusResponses, traceResponses } = setup();
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });
    facade.attach({ graph_id: 'graph-b', workflow_id: 'graph-b' });

    statusResponses[0].next(runtimeStatus({ workflowId: 'graph-a', runId: 'run-a' }));
    expect(traceResponses).toHaveLength(0);
    statusResponses[1].next(runtimeStatus({ workflowId: 'graph-b', runId: 'run-b' }));
    traceResponses[0].next(traceReadModel('graph-b', 'run-b'));
    traceResponses[0].complete();

    expect(facade.graphId()).toBe('graph-b');
    expect(facade.workflowId()).toBe('graph-b');
    expect(facade.runId()).toBe('run-b');
    expect(facade.runtimeOverlay()?.workflow_id).toBe('graph-b');
  });

  it('performs one final trace read for a terminal revision and then stops polling', () => {
    vi.useFakeTimers();
    const { facade, api, statusResponses, traceResponses } = setup({ intervalMs: 50 });
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });

    statusResponses[0].next(runtimeStatus({ revision: 8, status: 'completed' }));
    expect(traceResponses).toHaveLength(1);
    expect(facade.runtimeOverlay()).toBeNull();
    traceResponses[0].next(traceReadModel());
    traceResponses[0].complete();

    expect(facade.state()).toBe('terminal');
    expect(facade.runtimeOverlay()?.overall_status).toBe('completed');
    expect(facade.edgeTraceReadModel()?.run_id).toBe('run-a');
    vi.advanceTimersByTime(500);
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(1);
  });

  it('keeps polling a terminal run whose trace was projected at an older revision', () => {
    vi.useFakeTimers();
    const { facade, api, statusResponses, traceResponses } = setup({ intervalMs: 50 });
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });

    statusResponses[0].next(runtimeStatus({ revision: 8, status: 'completed' }));
    traceResponses[0].next(traceReadModel('graph-a', 'run-a', 7));
    traceResponses[0].complete();

    // A trace built before the run finished describes a run that had not
    // finished, however well formed it is.
    expect(facade.state()).toBe('terminal');
    vi.advanceTimersByTime(50);
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);

    statusResponses[1].next(runtimeStatus({ revision: 8, status: 'completed' }));
    traceResponses[1].next(traceReadModel('graph-a', 'run-a', 8));
    traceResponses[1].complete();

    vi.advanceTimersByTime(500);
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);
  });

  it('keeps polling when a Hub cannot stamp the revision it projected at', () => {
    vi.useFakeTimers();
    const { facade, api, statusResponses, traceResponses } = setup({ intervalMs: 50 });
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });

    statusResponses[0].next(runtimeStatus({ revision: 8, status: 'completed' }));
    traceResponses[0].next(traceReadModel('graph-a', 'run-a', null));
    traceResponses[0].complete();

    // Unstamped is unproven, never proven fresh.
    expect(facade.edgeTraceReadModel()?.source_revision).toBeNull();
    vi.advanceTimersByTime(50);
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);
  });

  it('keeps polling a terminal run whose trace projection is not yet verified', () => {
    vi.useFakeTimers();
    const { facade, api, statusResponses, traceResponses } = setup({ intervalMs: 50 });
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });

    statusResponses[0].next(runtimeStatus({ revision: 8, status: 'completed' }));
    traceResponses[0].next(unverifiedTraceReadModel());
    traceResponses[0].complete();

    // The payload decodes, so it is shown, but an incomplete correlation is not
    // the completion evidence that may end terminal polling.
    expect(facade.state()).toBe('terminal');
    expect(facade.edgeTraceReadModel()?.verification_status).toBe('unverified');
    vi.advanceTimersByTime(50);
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);

    statusResponses[1].next(runtimeStatus({ revision: 8, status: 'completed' }));
    traceResponses[1].next(traceReadModel());
    traceResponses[1].complete();

    expect(facade.edgeTraceReadModel()?.verification_status).toBe('verified');
    vi.advanceTimersByTime(500);
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);
  });

  it('keeps a terminal trace failure explicitly unavailable and retries until final trace arrives', () => {
    vi.useFakeTimers();
    const { facade, api, statusResponses, traceResponses } = setup({ intervalMs: 50 });
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });

    statusResponses[0].next(runtimeStatus({ revision: 8, status: 'completed' }));
    traceResponses[0].error(httpError(503));
    expect(facade.state()).toBe('terminal');
    expect(facade.runtimeOverlay()).not.toBeNull();
    expect(facade.edgeTraceReadModel()).toBeNull();
    expect(facade.errorCode()).toBe(
      'caseflow_runtime_terminal_caseflow_runtime_trace_unavailable',
    );

    vi.advanceTimersByTime(50);
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);
    statusResponses[1].next(runtimeStatus({ revision: 8, status: 'completed' }));
    traceResponses[1].next(traceReadModel());
    traceResponses[1].complete();

    expect(facade.edgeTraceReadModel()?.run_id).toBe('run-a');
    expect(facade.errorCode()).toBeNull();
    vi.advanceTimersByTime(500);
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);
  });

  it('detach immediately clears evidence and fences a late response', () => {
    const { facade, statusResponses, traceReader } = setup();
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });
    facade.detach();
    statusResponses[0].next(runtimeStatus());

    expect(facade.state()).toBe('detached');
    expect(facade.graphId()).toBeNull();
    expect(facade.runtimeOverlay()).toBeNull();
    expect(traceReader.read).not.toHaveBeenCalled();
  });

  it('revokeAccess stops polling, clears evidence and fences late responses', () => {
    const { facade, statusResponses, traceResponses } = setup();
    facade.attach({ graph_id: 'graph-a', workflow_id: 'graph-a' });
    completeCycle(statusResponses[0], traceResponses, runtimeStatus());
    facade.refresh();
    expect(statusResponses).toHaveLength(2);

    facade.revokeAccess('caseflow_inspector_access_revoked');
    statusResponses[1].next(runtimeStatus({ revision: 2 }));

    expect(facade.state()).toBe('access_revoked');
    expect(facade.errorCode()).toBe('caseflow_inspector_access_revoked');
    expect(facade.runtimeOverlay()).toBeNull();
    expect(facade.edgeTraceReadModel()).toBeNull();
    expect(facade.graphId()).toBe('graph-a');
    expect(facade.workflowId()).toBe('graph-a');
    expect(facade.canRefresh()).toBe(false);
  });
});

interface SetupOptions {
  readonly intervalMs?: number;
  readonly maxNotFoundPolls?: number;
}

function setup(options: SetupOptions = {}) {
  const statusResponses: Subject<Record<string, unknown>>[] = [];
  const traceResponses: Subject<Record<string, unknown>>[] = [];
  const api = {
    getWorkflowStatus: vi.fn(() => {
      const response = new Subject<Record<string, unknown>>();
      statusResponses.push(response);
      return response.asObservable();
    }),
  };
  const traceReader = {
    read: vi.fn(() => {
      const response = new Subject<Record<string, unknown>>();
      traceResponses.push(response);
      return response.asObservable();
    }),
  };
  TestBed.configureTestingModule({
    providers: [
      CaseFlowAgentRuntimeSessionFacade,
      { provide: VisualProcessApiService, useValue: api },
      { provide: CASEFLOW_EDGE_TRACE_READER, useValue: traceReader },
      {
        provide: CASEFLOW_AGENT_RUNTIME_SESSION_CONFIG,
        useValue: {
          poll_interval_ms: options.intervalMs ?? 60_000,
          max_initial_not_found_polls: options.maxNotFoundPolls ?? 5,
        },
      },
    ],
  });
  return {
    facade: TestBed.inject(CaseFlowAgentRuntimeSessionFacade),
    api,
    traceReader,
    statusResponses,
    traceResponses,
  };
}

function completeCycle(
  statusResponse: Subject<Record<string, unknown>>,
  traceResponses: Subject<Record<string, unknown>>[],
  status: Record<string, unknown>,
): void {
  statusResponse.next(status);
  const trace = traceResponses.at(-1);
  if (!trace) throw new Error('trace response expected');
  trace.next(traceReadModel(
    String(status['workflow_id'] ?? 'graph-a'),
    String(status['run_id'] ?? 'run-a'),
  ));
  trace.complete();
}

interface RuntimeStatusOverrides {
  readonly workflowId?: string;
  readonly runId?: string;
  readonly revision?: number;
  readonly status?: string;
}

function runtimeStatus(overrides: RuntimeStatusOverrides = {}): Record<string, unknown> {
  const workflowId = overrides.workflowId ?? 'graph-a';
  const status = overrides.status ?? 'running';
  const stepStatus = terminalStepStatus(status);
  return {
    schema: 'ananta.workflow_backend_status.v1',
    backend: 'hub',
    workflow_id: workflowId,
    run_id: overrides.runId ?? 'run-a',
    process_id: workflowId,
    revision: overrides.revision ?? 1,
    updated_at: overrides.revision ?? 1,
    status,
    steps: [{ step_id: 'builder', status: stepStatus }],
  };
}

function terminalStepStatus(status: string): string {
  if (['done', 'success', 'completed', 'succeeded'].includes(status)) return 'completed';
  if (['error', 'failed'].includes(status)) return 'failed';
  if (['cancelled', 'canceled'].includes(status)) return 'cancelled';
  if (status === 'skipped') return 'skipped';
  return 'running';
}

function unverifiedTraceReadModel(
  workflowId = 'graph-a',
  runId = 'run-a',
): Record<string, unknown> {
  return {
    ...traceReadModel(workflowId, runId),
    verification_status: 'unverified',
    reason_code: 'caseflow_edge_evidence_incomplete',
  };
}

function traceReadModel(
  workflowId = 'graph-a',
  runId = 'run-a',
  sourceRevision: number | null = 8,
): Record<string, unknown> {
  return {
    schema: 'ananta.caseflow_edge_trace_read_model.v1',
    workflow_id: workflowId,
    run_id: runId,
    catalog_verification_status: 'verified',
    verification_status: 'verified',
    reason_code: '',
    ...(sourceRevision === null ? {} : { source_revision: sourceRevision }),
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

function httpError(status: number): HttpErrorResponse {
  return new HttpErrorResponse({ status, statusText: `HTTP ${status}` });
}
