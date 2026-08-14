import { signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Subject, of, throwError } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { VisualProcessApiService, VpGraph } from './visual-process-api.service';
import {
  VP_WORKFLOW_RUNNER_CLOCK,
  VpWorkflowRunnerService,
} from './vp-workflow-runner.service';

describe('VpWorkflowRunnerService runtime overlay', () => {
  function setup() {
    const api = {
      getWorkflowStatus: vi.fn(),
      startWorkflowFromGraph: vi.fn(),
      cancelWorkflow: vi.fn(() => of({})),
      signalWorkflow: vi.fn(() => of({})),
    };
    TestBed.configureTestingModule({ providers: [
      VpWorkflowRunnerService,
      { provide: VisualProcessApiService, useValue: api },
      { provide: VP_WORKFLOW_RUNNER_CLOCK, useValue: { now: () => 123 } },
    ] });
    return { runner: TestBed.inject(VpWorkflowRunnerService), api };
  }

  it('maps runtime states without mutating the graph definition', () => {
    const { runner } = setup();
    const graph: VpGraph = { id:'vp-1', name:'Flow', description:'', version:'1', tags:[], edges:[], steps:[{ id:'s1', label:'One', kind:'task', io:{inputs:[],outputs:[]}, position:{x:0,y:0}, policy_hints:[], gate:false, metadata:{} }] };
    const before = JSON.stringify(graph);
    (runner as any).applyStatus({ schema:'ananta.workflow_backend_status.v1', backend:'local', workflow_id:'wf-1', run_id:'run-1', revision:1, updated_at:1, status:'running', steps:[{ step_id:'s1', run_state:'done', selected_model:'m1' }, { step_id:'late', run_state:'unknown' }] });
    expect(JSON.stringify(graph)).toBe(before);
    expect(runner.runtimeOverlay()!.steps['s1'].status).toBe('succeeded');
    expect(runner.runtimeOverlay()!.steps['late'].status).toBe('unknown');
  });

  it('normalizes Hub task states and keeps approval waits active in the overlay', () => {
    const { runner } = setup();
    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1',
      backend: 'local',
      workflow_id: 'wf-recovery',
      run_id: 'run-recovery',
      revision: 1,
      updated_at: 1,
      status: 'RUNNING',
      steps: [
        { step_id: 'completed', run_state: ' COMPLETED ' },
        { step_id: 'active', status: 'IN_PROGRESS' },
        { step_id: 'approval', status: 'WAITING_FOR_APPROVAL' },
        { step_id: 'review', run_state: 'waiting_for_review' },
      ],
    });

    const overlay = runner.runtimeOverlay()!;
    expect(overlay.steps['completed'].status).toBe('succeeded');
    expect(overlay.steps['active'].status).toBe('running');
    expect(overlay.steps['approval'].status).toBe('awaiting_approval');
    expect(overlay.steps['review'].status).toBe('awaiting_approval');
    expect(overlay.current_step_ids).toEqual(['active', 'approval', 'review']);
  });

  it.each(['COMPLETED', 'succeeded'])('stops polling for terminal success status %s', status => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of({
      schema: 'ananta.workflow_backend_status.v1', backend: 'local', workflow_id: 'wf-terminal', run_id: 'run-terminal', revision: 1, updated_at: 1, status, steps: [],
    }));

    runner.attach('wf-terminal');

    expect((runner as any).pollScopes.value).toBeNull();
    expect(runner.status()).toBe('Workflow abgeschlossen ✓');
    runner.destroy();
  });

  it('supports attach, refresh and detach while retaining the last overlay', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of({ schema:'ananta.workflow_backend_status.v1', backend:'local', workflow_id:'wf-1', run_id:'run-1', revision:1, updated_at:1, status:'done', steps:[] }));
    runner.attach('wf-1');
    expect(runner.runtimeOverlay()!.overall_status).toBe('done');
    runner.detach();
    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.runtimeOverlay()!.workflow_id).toBe('wf-1');
    runner.destroy();
  });

  it('projects the real LoRA job, phase and terminal result from step outputs', () => {
    const { runner } = setup();
    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1', backend: 'local', workflow_id: 'wf-training', run_id: 'run-training', revision: 1, updated_at: 1, status: 'done',
      steps: [{
        step_id: 'train', run_state: 'done', outputs: {
          job_id: 'job-17', dataset_id: 'dataset-9', training_profile_id: 'generic-safe',
          training_status: 'completed', training_phase: 'publishing', terminal: true,
          terminal_result: { adapter_id: 'adapter-3' },
        },
      }],
    });

    expect(runner.runtimeOverlay()!.steps['train'].training).toEqual(expect.objectContaining({
      jobId: 'job-17', datasetId: 'dataset-9', trainingProfileId: 'generic-safe',
      status: 'completed', phase: 'publishing', terminal: true,
      terminalResult: { adapter_id: 'adapter-3' },
      jobUrl: '/model-training?tab=jobs&job_id=job-17',
    }));
  });

  it('projects the canonical dataset ID and Control-Center link from a dataset-build step', () => {
    const { runner } = setup();
    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1', backend: 'local', workflow_id: 'wf-dataset', run_id: 'run-dataset', revision: 1, updated_at: 1, status: 'done',
      steps: [{
        step_id: 'build', run_state: 'done', outputs: {
          dataset_id: 'dataset-21', dataset_status: 'validated',
          dataset_build_result: {
            id: 'dataset-21', validation_status: 'passed', trainable: true,
            record_count: 10, train_record_count: 8, validation_record_count: 2,
          },
        },
        diagnostics: { source_mode: 'bounded_upstream_records' },
      }],
    });

    expect(runner.runtimeOverlay()!.steps['build'].datasetBuild).toEqual(expect.objectContaining({
      datasetId: 'dataset-21', status: 'validated', validationStatus: 'passed', trainable: true,
      trainRecordCount: 8, validationRecordCount: 2,
      datasetUrl: '/model-training?tab=datasets&dataset_id=dataset-21',
      sourceMode: 'bounded_upstream_records',
    }));
  });

  it('fails closed instead of substituting workflow_id for a missing run_id', () => {
    const { runner } = setup();

    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1', backend: 'local', workflow_id: 'wf-invalid', revision: 1, updated_at: 1, status: 'running', steps: [],
    });

    expect(runner.runtimeOverlay()).toBeNull();
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.status()).toContain('vp_runtime_run_id_invalid');
  });

  it('fails closed for an unrecognized status instead of guessing unknown', () => {
    const { runner } = setup();

    (runner as any).applyStatus({
      schema: 'ananta.workflow_backend_status.v1', backend: 'local', workflow_id: 'wf-invalid', run_id: 'run-invalid',
      revision: 1, updated_at: 1, status: 'running', steps: [{ step_id: 's1', status: 'surprising' }],
    });

    expect(runner.runtimeOverlay()).toBeNull();
    expect(runner.status()).toContain('vp_runtime_status_invalid');
  });

  it('binds a start response to the exact submitted graph identity', () => {
    const { runner, api } = setup();
    const graph: VpGraph = {
      id: 'graph-a', name: 'Flow', description: '', version: '1', tags: [], edges: [], steps: [],
    };
    api.startWorkflowFromGraph.mockReturnValue(of({
      schema: 'ananta.workflow_backend_status.v1', backend: 'local', workflow_id: 'graph-b', run_id: 'run-b',
      revision: 1, updated_at: 1, status: 'running', steps: [],
    }));

    runner.start(signal(graph));

    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
    expect(runner.status()).toContain('vp_runtime_workflow_scope_mismatch');
  });

  it('clears an earlier runtime projection before a new start that fails', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-old', 'run-old', 1)));
    runner.attach('graph-old');
    api.startWorkflowFromGraph.mockReturnValue(throwError(() => new Error('start unavailable')));

    runner.start(signal({
      id: 'graph-new', name: 'New Flow', description: '', version: '1', tags: [], edges: [], steps: [],
    }));

    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
    expect(runner.status()).toContain('Workflow konnte nicht gestartet werden');
  });

  it('reuses one start command ID after an ambiguous transport failure', () => {
    const { runner, api } = setup();
    const graph: VpGraph = {
      id: 'graph-a', name: 'Flow', description: '', version: '1', tags: [], edges: [], steps: [],
    };
    api.startWorkflowFromGraph
      .mockReturnValueOnce(throwError(() => ({ status: 503 })))
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));

    runner.start(signal(graph));
    const firstCommandId = api.startWorkflowFromGraph.mock.calls[0][1].command_id;
    runner.start(signal(graph));

    expect(firstCommandId).toMatch(/^vp-start-[A-Za-z0-9-]+$/);
    expect(api.startWorkflowFromGraph.mock.calls[1][1].command_id).toBe(firstCommandId);
    expect(runner.activeWorkflowId()).toBe('graph-a');
    expect((runner as any).pendingStart).toBeNull();
  });

  it('does not duplicate a start request while the first request is in flight', () => {
    const { runner, api } = setup();
    const response = new Subject<Record<string, unknown>>();
    const graph: VpGraph = {
      id: 'graph-a', name: 'Flow', description: '', version: '1', tags: [], edges: [], steps: [],
    };
    api.startWorkflowFromGraph.mockReturnValue(response);

    expect(runner.start(signal(graph))).toBe(true);
    expect(runner.start(signal(graph))).toBe(false);

    expect(api.startWorkflowFromGraph).toHaveBeenCalledTimes(1);
    expect(runner.status()).toBe('Workflow-Start wird bereits verarbeitet…');
  });

  it('clears an earlier runtime projection while a newly attached scope is loading', () => {
    const { runner, api } = setup();
    const newStatus = new Subject<Record<string, unknown>>();
    api.getWorkflowStatus.mockImplementation((workflowId: string) => workflowId === 'graph-old'
      ? of(runtimeStatus('graph-old', 'run-old', 1))
      : newStatus);
    runner.attach('graph-old');

    runner.attach('graph-new');

    expect(runner.activeWorkflowId()).toBe('graph-new');
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
    newStatus.next(runtimeStatus('graph-new', 'run-new', 1));
    expect(runner.runtimeOverlay()?.run_id).toBe('run-new');
  });

  it('does not send cancel before attach has established an exact run and revision', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(new Subject<Record<string, unknown>>());

    runner.attach('graph-a');
    runner.cancel();

    expect(api.cancelWorkflow).not.toHaveBeenCalled();
    expect(runner.status()).toBe('Abbrechen erst nach bestätigtem Workflow-Status möglich');
    expect((runner as any).pollScopes.value).not.toBeNull();
  });

  it('does not send a gate signal before attach has established an exact run and revision', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(new Subject<Record<string, unknown>>());

    runner.attach('graph-a');
    runner.signalGate('approve', 'gate-a');

    expect(api.signalWorkflow).not.toHaveBeenCalled();
    expect(runner.status()).toBe('Gate-Signal erst nach bestätigtem Workflow-Status möglich');
    expect((runner as any).pollScopes.value).not.toBeNull();
  });

  it('serializes status reads and ignores refreshes while one is in flight', () => {
    const { runner, api } = setup();
    const responses: Subject<Record<string, unknown>>[] = [];
    api.getWorkflowStatus.mockImplementation(() => {
      const response = new Subject<Record<string, unknown>>();
      responses.push(response);
      return response;
    });

    runner.attach('graph-a');
    runner.refresh();
    runner.refresh();
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(1);

    responses[0].next(runtimeStatus('graph-a', 'run-a', 1));
    responses[0].complete();
    runner.refresh();
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);
  });

  it('fences a late response after attach switches workflow identity', () => {
    const { runner, api } = setup();
    const responses: Subject<Record<string, unknown>>[] = [];
    api.getWorkflowStatus.mockImplementation(() => {
      const response = new Subject<Record<string, unknown>>();
      responses.push(response);
      return response;
    });

    runner.attach('graph-a');
    runner.attach('graph-b');
    responses[0].next(runtimeStatus('graph-a', 'run-a', 9));
    responses[1].next(runtimeStatus('graph-b', 'run-b', 1));

    expect(runner.activeWorkflowId()).toBe('graph-b');
    expect(runner.runtimeOverlay()?.workflow_id).toBe('graph-b');
    expect(runner.runtimeOverlay()?.run_id).toBe('run-b');
  });

  it('fences a late response after detach', () => {
    const { runner, api } = setup();
    const response = new Subject<Record<string, unknown>>();
    api.getWorkflowStatus.mockReturnValue(response);
    runner.attach('graph-a');
    runner.detach();

    response.next(runtimeStatus('graph-a', 'run-a', 1));

    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
  });

  it('clears retained runtime evidence when its graph definition is replaced', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    runner.attach('graph-a');

    runner.clearRuntimeScope();

    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
    expect((runner as any).pollScopes.value).toBeNull();
  });

  it.each([401, 403, 404])(
    'revokes accepted runtime evidence after HTTP %s and fences a late command response',
    httpStatus => {
      const { runner, api } = setup();
      const lateCancel = new Subject<Record<string, unknown>>();
      api.getWorkflowStatus
        .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 1)))
        .mockReturnValueOnce(throwError(() => ({ status: httpStatus })));
      api.cancelWorkflow.mockReturnValue(lateCancel);
      runner.attach('graph-a');
      runner.cancel();

      runner.refresh();

      expect(runner.activeWorkflowId()).toBeNull();
      expect(runner.workflowStatus()).toBeNull();
      expect(runner.runtimeOverlay()).toBeNull();
      expect((runner as any).pollScopes.value).toBeNull();
      expect(runner.status()).toContain(`vp_runtime_access_revoked_${httpStatus}`);

      lateCancel.next(runtimeStatus('graph-a', 'run-a', 2, 'cancelled'));
      lateCancel.complete();
      expect(runner.runtimeOverlay()).toBeNull();
      expect(runner.status()).toContain(`vp_runtime_access_revoked_${httpStatus}`);
    },
  );

  it.each([401, 403, 404])(
    'revokes accepted runtime evidence immediately after cancel HTTP %s',
    httpStatus => {
      const { runner, api } = setup();
      const latePoll = new Subject<Record<string, unknown>>();
      api.getWorkflowStatus
        .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 1)))
        .mockReturnValueOnce(latePoll);
      api.cancelWorkflow.mockReturnValue(throwError(() => ({ status: httpStatus })));
      runner.attach('graph-a');
      runner.refresh();

      runner.cancel();

      expect(runner.activeWorkflowId()).toBeNull();
      expect(runner.workflowStatus()).toBeNull();
      expect(runner.runtimeOverlay()).toBeNull();
      expect((runner as any).pollScopes.value).toBeNull();
      expect(runner.status()).toContain(`vp_runtime_access_revoked_${httpStatus}`);

      latePoll.next(runtimeStatus('graph-a', 'run-a', 2));
      latePoll.complete();
      expect(runner.runtimeOverlay()).toBeNull();
      expect(runner.status()).toContain(`vp_runtime_access_revoked_${httpStatus}`);
    },
  );

  it('revokes accepted runtime evidence immediately after a forbidden gate command', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.signalWorkflow.mockReturnValue(throwError(() => ({ status: 403 })));
    runner.attach('graph-a');

    runner.signalGate('approve', 'gate-a');

    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
    expect((runner as any).pollScopes.value).toBeNull();
    expect(runner.status()).toContain('vp_runtime_access_revoked_403');
  });

  it('retires stale running evidence on polling timeout and fences a late response', () => {
    const { runner, api } = setup();
    const latePoll = new Subject<Record<string, unknown>>();
    api.getWorkflowStatus
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 1)))
      .mockReturnValueOnce(latePoll);
    runner.attach('graph-a');
    runner.refresh();
    const fence = {
      ...(runner as any).pollScopes.value,
      expected_run_id: 'run-a',
      minimum_revision: 1,
    };

    (runner as any).acceptPollResult({ kind: 'timeout', fence });

    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
    expect((runner as any).pollScopes.value).toBeNull();
    expect(runner.status()).toBe('Polling-Timeout (10 min) — Workflow-Status unbekannt');
    runner.signalGate('approve', 'gate-a');
    expect(api.signalWorkflow).not.toHaveBeenCalled();

    latePoll.next(runtimeStatus('graph-a', 'run-a', 2));
    latePoll.complete();
    expect(runner.runtimeOverlay()).toBeNull();
    expect(runner.status()).toBe('Polling-Timeout (10 min) — Workflow-Status unbekannt');
  });

  it('keeps an initial HTTP 404 bounded by polling and accepts the later exact run', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus
      .mockReturnValueOnce(throwError(() => ({ status: 404 })))
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 1)));

    runner.attach('graph-a');

    expect(runner.activeWorkflowId()).toBe('graph-a');
    expect(runner.runtimeOverlay()).toBeNull();
    expect((runner as any).pollScopes.value).not.toBeNull();
    expect(runner.status()).toContain('noch nicht verfügbar');

    runner.refresh();
    expect(runner.runtimeOverlay()?.run_id).toBe('run-a');
  });

  it('binds later polls to the first run and ignores older revisions', () => {
    const { runner, api } = setup();
    const responses: Subject<Record<string, unknown>>[] = [];
    api.getWorkflowStatus.mockImplementation(() => {
      const response = new Subject<Record<string, unknown>>();
      responses.push(response);
      return response;
    });
    runner.attach('graph-a');
    responses[0].next(runtimeStatus('graph-a', 'run-a', 2, 'running'));
    responses[0].complete();

    runner.refresh();
    responses[1].next(runtimeStatus('graph-a', 'run-a', 1, 'failed'));
    responses[1].complete();
    expect(runner.runtimeOverlay()?.overall_status).toBe('running');

    runner.refresh();
    responses[2].next(runtimeStatus('graph-a', 'run-other', 3, 'running'));
    responses[2].complete();
    expect(runner.runtimeOverlay()).toBeNull();
    expect(runner.status()).toContain('vp_runtime_run_scope_mismatch');
  });

  it('atomically retires a run after an invalid later poll and blocks stale commands', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 1)))
      .mockReturnValueOnce(of({
        ...runtimeStatus('graph-a', 'run-a', 2),
        run_id: 'foreign-run',
      }));
    runner.attach('graph-a');

    runner.refresh();
    runner.cancel();
    runner.signalGate('approve', 'gate-a');

    expect(runner.activeWorkflowId()).toBeNull();
    expect(runner.workflowStatus()).toBeNull();
    expect(runner.runtimeOverlay()).toBeNull();
    expect((runner as any).activeRunId).toBeNull();
    expect((runner as any).acceptedRevision).toBeNull();
    expect((runner as any).pollScopes.value).toBeNull();
    expect(runner.status()).toContain('vp_runtime_run_scope_mismatch');
    expect(api.cancelWorkflow).not.toHaveBeenCalled();
    expect(api.signalWorkflow).not.toHaveBeenCalled();
  });

  it('ignores a delayed cancel response after attaching a different workflow', () => {
    const { runner, api } = setup();
    const cancelResponse = new Subject<Record<string, unknown>>();
    api.getWorkflowStatus.mockImplementation((workflowId: string) => of(
      workflowId === 'graph-a'
        ? runtimeStatus('graph-a', 'run-a', 1)
        : runtimeStatus('graph-b', 'run-b', 1),
    ));
    api.cancelWorkflow.mockReturnValue(cancelResponse);

    runner.attach('graph-a');
    runner.cancel();
    runner.attach('graph-b');
    cancelResponse.next(runtimeStatus('graph-a', 'run-a', 2, 'cancelled'));
    cancelResponse.complete();

    expect(runner.activeWorkflowId()).toBe('graph-b');
    expect(runner.runtimeOverlay()).toMatchObject({
      workflow_id: 'graph-b',
      run_id: 'run-b',
      overall_status: 'running',
    });
    expect((runner as any).pollScopes.value).not.toBeNull();
    expect(runner.status()).not.toBe('Workflow abgebrochen');
  });

  it('accepts cancel_requested as nonterminal and continues serialized polling', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.cancelWorkflow.mockReturnValue(of(
      runtimeStatus('graph-a', 'run-a', 2, 'cancel_requested'),
    ));

    runner.attach('graph-a');
    runner.cancel();

    expect(runner.runtimeOverlay()?.overall_status).toBe('cancel_requested');
    expect(runner.workflowStatus()?.['revision']).toBe(2);
    expect(runner.status()).toBe('Abbruch angefordert…');
    expect((runner as any).pollScopes.value).not.toBeNull();

    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 3)));
    runner.refresh();
    expect(api.getWorkflowStatus).toHaveBeenCalledTimes(2);
    expect(runner.workflowStatus()?.['revision']).toBe(3);
  });

  it('does not let an older in-flight poll failure erase an accepted cancel projection', () => {
    const { runner, api } = setup();
    const olderPoll = new Subject<Record<string, unknown>>();
    api.getWorkflowStatus
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 1)))
      .mockReturnValueOnce(olderPoll);
    api.cancelWorkflow.mockReturnValue(of(
      runtimeStatus('graph-a', 'run-a', 2, 'cancel_requested'),
    ));

    runner.attach('graph-a');
    runner.refresh();
    runner.cancel();
    olderPoll.next({
      ...runtimeStatus('graph-a', 'run-a', 1),
      run_id: undefined,
    });
    olderPoll.complete();

    expect(runner.workflowStatus()?.['revision']).toBe(2);
    expect(runner.runtimeOverlay()).toMatchObject({
      workflow_id: 'graph-a',
      run_id: 'run-a',
      overall_status: 'cancel_requested',
    });
    expect((runner as any).pollScopes.value).not.toBeNull();
    expect(runner.status()).toBe('Abbruch angefordert…');
  });

  it('accepts a terminal cancel projection and stops polling truthfully', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.cancelWorkflow.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 2, 'cancelled')));

    runner.attach('graph-a');
    runner.cancel();

    expect(runner.runtimeOverlay()?.overall_status).toBe('cancelled');
    expect(runner.status()).toBe('Workflow abgebrochen');
    expect((runner as any).pollScopes.value).toBeNull();
  });

  it.each([
    [{ ...runtimeStatus('graph-a', 'run-a', 2), run_id: undefined }, 'vp_runtime_run_id_invalid'],
    [{ ...runtimeStatus('graph-a', 'run-other', 2) }, 'vp_runtime_run_scope_mismatch'],
    [{ ...runtimeStatus('graph-a', 'run-a', 2), revision: undefined }, 'vp_runtime_revision_required'],
  ])('rejects an invalid cancel runtime contract without replacing known state: %s', (response, reason) => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.cancelWorkflow.mockReturnValue(of(response));

    runner.attach('graph-a');
    runner.cancel();

    expect(runner.runtimeOverlay()).toMatchObject({
      workflow_id: 'graph-a',
      run_id: 'run-a',
      overall_status: 'running',
    });
    expect(runner.workflowStatus()?.['revision']).toBe(1);
    expect(runner.status()).toContain(reason as string);
    expect((runner as any).pollScopes.value).not.toBeNull();
  });

  it('discards a lower-revision cancel response without regressing the runtime projection', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 5)));
    api.cancelWorkflow.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 4, 'cancelled')));

    runner.attach('graph-a');
    runner.cancel();

    expect(runner.workflowStatus()?.['revision']).toBe(5);
    expect(runner.runtimeOverlay()?.overall_status).toBe('running');
    expect((runner as any).pollScopes.value).not.toBeNull();
    expect(runner.status()).not.toBe('Workflow abgebrochen');
  });

  it('does not treat a same-revision cancel payload as command acknowledgement', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 5)));
    api.cancelWorkflow.mockReturnValue(of(
      runtimeStatus('graph-a', 'run-a', 5, 'cancel_requested'),
    ));

    runner.attach('graph-a');
    runner.cancel();

    expect(runner.workflowStatus()?.['revision']).toBe(5);
    expect(runner.runtimeOverlay()?.overall_status).toBe('running');
    expect((runner as any).pollScopes.value).not.toBeNull();
    expect(runner.status()).toBe('Abbruch wird bestätigt…');
  });

  it('reuses one command ID when an ambiguous cancel transport failure is retried', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.cancelWorkflow
      .mockReturnValueOnce(throwError(() => ({ status: 503 })))
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 2, 'cancel_requested')));

    runner.attach('graph-a');
    runner.cancel();
    const firstCommandId = api.cancelWorkflow.mock.calls[0][2];
    runner.cancel();

    expect(firstCommandId).toMatch(/^vp-cancel-[A-Za-z0-9-]+$/);
    expect(api.cancelWorkflow.mock.calls[1][2]).toBe(firstCommandId);
    expect(runner.workflowStatus()?.['revision']).toBe(2);
    expect((runner as any).pendingCommand).toBeNull();
  });

  it('releases a deterministically rejected cancel so a later command can proceed', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.cancelWorkflow.mockReturnValue(throwError(() => ({
      status: 409,
      error: { detail: 'workflow_control_command_rejected' },
    })));
    api.signalWorkflow.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 2)));

    runner.attach('graph-a');
    runner.cancel();

    expect((runner as any).pendingCommand).toBeNull();

    runner.signalGate('approve', 'gate-a');

    expect(api.signalWorkflow).toHaveBeenCalledOnce();
    expect((runner as any).pendingCommand).toBeNull();
    expect(runner.workflowStatus()?.['revision']).toBe(2);
  });

  it('keeps a same-revision accepted cancel pending until polling observes a newer revision', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 5)))
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 6, 'cancel_requested')));
    api.cancelWorkflow.mockReturnValue(of(
      runtimeStatus('graph-a', 'run-a', 5, 'cancel_requested'),
    ));

    runner.attach('graph-a');
    runner.cancel();
    const pendingCommandId = api.cancelWorkflow.mock.calls[0][2];
    expect((runner as any).pendingCommand?.command_id).toBe(pendingCommandId);

    runner.refresh();

    expect(runner.workflowStatus()?.['revision']).toBe(6);
    expect((runner as any).pendingCommand).toBeNull();
  });

  it('blocks a different mutation while an ambiguous command remains pending', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.cancelWorkflow.mockReturnValue(throwError(() => ({ status: 503 })));

    runner.attach('graph-a');
    runner.cancel();
    runner.signalGate('approve', 'gate-a');

    expect(api.signalWorkflow).not.toHaveBeenCalled();
    expect(runner.status()).toBe('Ein anderer Workflow-Befehl wartet noch auf Bestätigung');
  });

  it('ignores a delayed cancel error after attaching a different workflow', () => {
    const { runner, api } = setup();
    const cancelResponse = new Subject<Record<string, unknown>>();
    api.getWorkflowStatus.mockImplementation((workflowId: string) => of(
      workflowId === 'graph-a'
        ? runtimeStatus('graph-a', 'run-a', 1)
        : runtimeStatus('graph-b', 'run-b', 1),
    ));
    api.cancelWorkflow.mockReturnValue(cancelResponse);

    runner.attach('graph-a');
    runner.cancel();
    runner.attach('graph-b');
    cancelResponse.error(new Error('late cancel failure'));

    expect(runner.activeWorkflowId()).toBe('graph-b');
    expect(runner.runtimeOverlay()?.run_id).toBe('run-b');
    expect(runner.status()).not.toBe('Abbrechen fehlgeschlagen');
  });

  it('ignores a delayed cancel error after a newer revision was accepted in the same run', () => {
    const { runner, api } = setup();
    const pollResponse = new Subject<Record<string, unknown>>();
    const cancelResponse = new Subject<Record<string, unknown>>();
    api.getWorkflowStatus
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 1)))
      .mockReturnValueOnce(pollResponse);
    api.cancelWorkflow.mockReturnValue(cancelResponse);

    runner.attach('graph-a');
    runner.cancel();
    runner.refresh();
    pollResponse.next(runtimeStatus('graph-a', 'run-a', 2));
    pollResponse.complete();
    cancelResponse.error(new Error('outdated cancel failure'));

    expect(runner.workflowStatus()?.['revision']).toBe(2);
    expect(runner.runtimeOverlay()?.run_id).toBe('run-a');
    expect(runner.status()).not.toBe('Abbrechen fehlgeschlagen');
  });

  it('updates a nonterminal gate response and keeps polling', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.signalWorkflow.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 2, 'running')));

    runner.attach('graph-a');
    runner.signalGate('approve', 'gate-a');

    expect(runner.workflowStatus()?.['revision']).toBe(2);
    expect(runner.runtimeOverlay()?.overall_status).toBe('running');
    expect(runner.status()).toBe('Gate genehmigt ✓');
    expect((runner as any).pollScopes.value).not.toBeNull();
  });

  it('does not treat a same-revision gate payload as command acknowledgement', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 5)));
    api.signalWorkflow.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 5, 'running')));

    runner.attach('graph-a');
    runner.signalGate('approve', 'gate-a');

    expect(runner.workflowStatus()?.['revision']).toBe(5);
    expect(runner.runtimeOverlay()?.overall_status).toBe('running');
    expect((runner as any).pollScopes.value).not.toBeNull();
    expect(runner.status()).toBe('Gate-Genehmigung wird bestätigt…');
  });

  it('reuses one command ID for a same-direction gate retry after an ambiguous failure', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.signalWorkflow
      .mockReturnValueOnce(throwError(() => ({ status: 0 })))
      .mockReturnValueOnce(of(runtimeStatus('graph-a', 'run-a', 2, 'running')));

    runner.attach('graph-a');
    runner.signalGate('approve', 'gate-a');
    const firstCommandId = api.signalWorkflow.mock.calls[0][3];
    runner.signalGate('approve', 'gate-a');

    expect(firstCommandId).toMatch(/^vp-gate-[A-Za-z0-9-]+$/);
    expect(api.signalWorkflow.mock.calls[1][3]).toBe(firstCommandId);
    expect(runner.workflowStatus()?.['revision']).toBe(2);
    expect((runner as any).pendingCommand).toBeNull();
  });

  it('accepts a terminal gate response and stops with its authoritative outcome', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.signalWorkflow.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 2, 'failed')));

    runner.attach('graph-a');
    runner.signalGate('reject', 'gate-a');

    expect(runner.runtimeOverlay()?.overall_status).toBe('failed');
    expect(runner.status()).toBe('Workflow fehlgeschlagen');
    expect((runner as any).pollScopes.value).toBeNull();
  });

  it('treats a skipped command projection as terminal instead of restarting polling', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.signalWorkflow.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 2, 'skipped')));

    runner.attach('graph-a');
    runner.signalGate('reject', 'gate-a');

    expect(runner.runtimeOverlay()?.overall_status).toBe('skipped');
    expect(runner.status()).toBe('Workflow übersprungen');
    expect((runner as any).pollScopes.value).toBeNull();
  });

  it('ignores a delayed gate response after attaching a different workflow', () => {
    const { runner, api } = setup();
    const gateResponse = new Subject<Record<string, unknown>>();
    api.getWorkflowStatus.mockImplementation((workflowId: string) => of(
      workflowId === 'graph-a'
        ? runtimeStatus('graph-a', 'run-a', 1)
        : runtimeStatus('graph-b', 'run-b', 1),
    ));
    api.signalWorkflow.mockReturnValue(gateResponse);

    runner.attach('graph-a');
    runner.signalGate('approve', 'gate-a');
    runner.attach('graph-b');
    gateResponse.next(runtimeStatus('graph-a', 'run-a', 2, 'failed'));
    gateResponse.complete();

    expect(runner.activeWorkflowId()).toBe('graph-b');
    expect(runner.runtimeOverlay()).toMatchObject({
      workflow_id: 'graph-b',
      run_id: 'run-b',
      overall_status: 'running',
    });
    expect((runner as any).pollScopes.value).not.toBeNull();
    expect(runner.status()).not.toBe('Workflow fehlgeschlagen');
  });

  it('rejects a mismatched gate run without replacing the accepted projection', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of(runtimeStatus('graph-a', 'run-a', 1)));
    api.signalWorkflow.mockReturnValue(of(runtimeStatus('graph-a', 'run-other', 2, 'running')));

    runner.attach('graph-a');
    runner.signalGate('approve', 'gate-a');

    expect(runner.runtimeOverlay()?.run_id).toBe('run-a');
    expect(runner.workflowStatus()?.['revision']).toBe(1);
    expect(runner.status()).toContain('vp_runtime_run_scope_mismatch');
    expect((runner as any).pollScopes.value).not.toBeNull();
  });
});

function runtimeStatus(
  workflowId: string,
  runId: string,
  revision: number,
  status = 'running',
): Record<string, unknown> {
  return {
    schema: 'ananta.workflow_backend_status.v1',
    backend: 'hub',
    workflow_id: workflowId,
    run_id: runId,
    process_id: workflowId,
    revision,
    updated_at: revision,
    status,
    steps: [],
  };
}
