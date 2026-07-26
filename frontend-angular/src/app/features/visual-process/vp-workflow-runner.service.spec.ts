import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';
import { describe, expect, it, vi } from 'vitest';
import { VisualProcessApiService, VpGraph } from './visual-process-api.service';
import { VpWorkflowRunnerService } from './vp-workflow-runner.service';

describe('VpWorkflowRunnerService runtime overlay', () => {
  function setup() {
    const api = { getWorkflowStatus: vi.fn(), cancelWorkflow: vi.fn(() => of({})), signalWorkflow: vi.fn(() => of({})) };
    TestBed.configureTestingModule({ providers: [VpWorkflowRunnerService, { provide: VisualProcessApiService, useValue: api }] });
    return { runner: TestBed.inject(VpWorkflowRunnerService), api };
  }

  it('maps runtime states without mutating the graph definition', () => {
    const { runner } = setup();
    const graph: VpGraph = { id:'vp-1', name:'Flow', description:'', version:'1', tags:[], edges:[], steps:[{ id:'s1', label:'One', kind:'task', io:{inputs:[],outputs:[]}, position:{x:0,y:0}, policy_hints:[], gate:false, metadata:{} }] };
    const before = JSON.stringify(graph);
    (runner as any).applyStatus({ schema:'1', backend:'local', workflow_id:'wf-1', status:'running', steps:[{ step_id:'s1', run_state:'done', selected_model:'m1' }, { step_id:'late', run_state:'unexpected' }] });
    expect(JSON.stringify(graph)).toBe(before);
    expect(runner.runtimeOverlay()!.steps['s1'].status).toBe('succeeded');
    expect(runner.runtimeOverlay()!.steps['late'].status).toBe('unknown');
  });

  it('normalizes Hub task states and keeps approval waits active in the overlay', () => {
    const { runner } = setup();
    (runner as any).applyStatus({
      schema: '1',
      backend: 'local',
      workflow_id: 'wf-recovery',
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
      schema: '1', backend: 'local', workflow_id: 'wf-terminal', status, steps: [],
    }));

    runner.attach('wf-terminal');

    expect((runner as any).pollHandle).toBeNull();
    expect(runner.status()).toBe('Workflow abgeschlossen ✓');
    runner.destroy();
  });

  it('supports attach, refresh and detach while retaining the last overlay', () => {
    const { runner, api } = setup();
    api.getWorkflowStatus.mockReturnValue(of({ schema:'1', backend:'local', workflow_id:'wf-1', status:'done', steps:[] }));
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
      schema: '1', backend: 'local', workflow_id: 'wf-training', status: 'done',
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
      schema: '1', backend: 'local', workflow_id: 'wf-dataset', status: 'done',
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
});
