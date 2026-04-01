import { TestBed } from '@angular/core/testing';
import { of } from 'rxjs';

import { HubApiService } from '../../services/hub-api.service';
import { HubLiveStateService } from '../../services/hub-live-state.service';
import { ControlPlaneFacade } from './control-plane.facade';

describe('ControlPlaneFacade', () => {
  let facade: ControlPlaneFacade;
  let hubApi: Record<string, ReturnType<typeof vi.fn>>;
  let liveState: Record<string, ReturnType<typeof vi.fn>>;

  beforeEach(() => {
    hubApi = {
      getDashboardReadModel: vi.fn(() => of({ tasks: { counts: {} } })),
      getStatsHistory: vi.fn(() => of([])),
      listTeams: vi.fn(() => of([])),
      listTeamRoles: vi.fn(() => of([])),
      listAgents: vi.fn(() => of([])),
      getAutopilotStatus: vi.fn(() => of({ enabled: true })),
      startAutopilot: vi.fn(() => of({ enabled: true })),
      stopAutopilot: vi.fn(() => of({ enabled: false })),
      tickAutopilot: vi.fn(() => of({ enabled: true })),
      getTaskTimeline: vi.fn(() => of({ items: [] })),
      getLlmBenchmarks: vi.fn(() => of({ items: [] })),
      planGoal: vi.fn(() => of({ created_task_ids: ['T-1'] })),
      getTaskOrchestrationReadModel: vi.fn(() => of({ queue: {} })),
      ingestOrchestrationTask: vi.fn(() => of({ ok: true })),
      claimOrchestrationTask: vi.fn(() => of({ ok: true })),
      completeOrchestrationTask: vi.fn(() => of({ ok: true })),
      listGoals: vi.fn(() => of([])),
      configureAutoPlanner: vi.fn(() => of({ enabled: true })),
      createGoal: vi.fn(() => of({ goal: { id: 'G-1' } })),
      getGoalDetail: vi.fn(() => of({ goal: { id: 'G-1' } })),
      getGoalGovernanceSummary: vi.fn(() => of({ goal_id: 'G-1', verification: { total: 1 } })),
      patchGoalPlanNode: vi.fn(() => of({ ok: true })),
    };
    liveState = {
      ensureSystemEvents: vi.fn(),
      disconnectSystemEvents: vi.fn(),
      systemStreamConnected: vi.fn(() => true),
      lastSystemEvent: vi.fn(() => ({ type: 'token_rotated' })),
    };

    TestBed.configureTestingModule({
      providers: [
        ControlPlaneFacade,
        { provide: HubApiService, useValue: hubApi },
        { provide: HubLiveStateService, useValue: liveState },
      ],
    });
    facade = TestBed.inject(ControlPlaneFacade);
  });

  it('exposes live system state through the control-plane seam', () => {
    facade.ensureSystemEvents('http://hub:5000');

    expect(liveState.ensureSystemEvents).toHaveBeenCalledWith('http://hub:5000');
    expect(facade.systemStreamConnected()).toBe(true);
    expect(facade.lastSystemEvent()).toEqual({ type: 'token_rotated' });
  });

  it('delegates dashboard, orchestration and goal operations', () => {
    facade.getDashboardReadModel('http://hub:5000', { benchmarkTaskKind: 'analysis', includeTaskSnapshot: true }).subscribe();
    facade.getTaskOrchestrationReadModel('http://hub:5000').subscribe();
    facade.createGoal('http://hub:5000', { goal: 'Improve control plane' }).subscribe();
    facade.getGoalGovernanceSummary('http://hub:5000', 'G-1').subscribe();

    expect(hubApi.getDashboardReadModel).toHaveBeenCalledWith(
      'http://hub:5000',
      { benchmarkTaskKind: 'analysis', includeTaskSnapshot: true },
      undefined,
      undefined,
    );
    expect(hubApi.getTaskOrchestrationReadModel).toHaveBeenCalledWith('http://hub:5000', undefined);
    expect(hubApi.createGoal).toHaveBeenCalledWith('http://hub:5000', { goal: 'Improve control plane' }, undefined);
    expect(hubApi.getGoalGovernanceSummary).toHaveBeenCalledWith('http://hub:5000', 'G-1', undefined);
  });
});
