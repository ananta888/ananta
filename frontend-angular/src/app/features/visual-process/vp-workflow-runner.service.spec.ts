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
});
