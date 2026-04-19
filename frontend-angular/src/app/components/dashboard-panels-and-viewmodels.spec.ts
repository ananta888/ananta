import { TestBed } from '@angular/core/testing';
import { of, throwError } from 'rxjs';

import { ControlPlaneFacade } from '../features/control-plane/control-plane.facade';
import { GoalDetail, GoalGovernanceSummary, GoalListEntry } from '../models/dashboard.models';
import { NotificationService } from '../services/notification.service';
import { DashboardGoalReportingFacade } from './dashboard-goal-reporting.facade';
import { DashboardWorkspaceViewModelService } from './dashboard-workspace-view-model.service';

describe('dashboard workspace view models', () => {
  it('counts only unfinished next tasks and ignores invalid inputs', () => {
    const service = new DashboardWorkspaceViewModelService();

    expect(service.nextTaskCount([
      { status: 'open' },
      { status: 'in_progress' },
      { status: 'blocked' },
      { status: 'completed' },
      { status: 'DONE' },
    ])).toBe(3);
    expect(service.nextTaskCount(null)).toBe(0);
  });

  it('reports starter progress from first-start, goal and task signals', () => {
    const service = new DashboardWorkspaceViewModelService();

    expect(service.starterProgress({
      firstStartCompleted: false,
      goals: [],
      hasQuickGoalResult: false,
      nextTaskCount: 0,
      createdTaskCount: 0,
    })).toEqual({ done: 0, total: 3, label: 'Naechster Schritt bleibt sichtbar.' });

    expect(service.starterProgress({
      firstStartCompleted: true,
      goals: [{ id: 'goal-1' }],
      hasQuickGoalResult: false,
      nextTaskCount: 0,
      createdTaskCount: 2,
    })).toEqual({ done: 3, total: 3, label: 'Erste Nutzung ist vorbereitet.' });
  });
});

describe('dashboard goal reporting facade', () => {
  const goals: GoalListEntry[] = [
    { id: 'old', summary: 'Old goal', updated_at: 10 },
    { id: 'new', summary: 'New goal', updated_at: 30 },
    { id: 'created', summary: 'Created goal', created_at: 20 },
  ];
  const detail: GoalDetail = {
    goal: { id: 'new', summary: 'New goal' },
    tasks: [
      { id: 'cheap', cost_summary: { cost_units: 1 } },
      { id: 'expensive', cost_summary: { cost_units: 5 } },
      { id: 'free', cost_summary: { cost_units: 0 } },
    ],
  };
  const governance: GoalGovernanceSummary = { goal_id: 'new', summary: { task_count: 3 } };

  afterEach(() => TestBed.resetTestingModule());

  function setup(overrides: Partial<Record<'listGoals' | 'getGoalDetail' | 'getGoalGovernanceSummary', ReturnType<typeof vi.fn>>> = {}) {
    const hubApi = {
      listGoals: overrides.listGoals || vi.fn(() => of(goals)),
      getGoalDetail: overrides.getGoalDetail || vi.fn(() => of(detail)),
      getGoalGovernanceSummary:
        overrides.getGoalGovernanceSummary || vi.fn(() => of(governance)),
    };
    const notifications = {
      error: vi.fn(),
    };
    TestBed.configureTestingModule({
      providers: [
        DashboardGoalReportingFacade,
        { provide: ControlPlaneFacade, useValue: hubApi },
        { provide: NotificationService, useValue: notifications },
      ],
    });
    return {
      facade: TestBed.inject(DashboardGoalReportingFacade),
      hubApi,
      notifications,
    };
  }

  it('sorts goals, loads the selected detail and exposes cost-heavy tasks first', () => {
    const { facade, hubApi } = setup();

    facade.refresh('http://hub:5000', 'new');

    expect(hubApi.listGoals).toHaveBeenCalledWith('http://hub:5000');
    expect(hubApi.getGoalDetail).toHaveBeenCalledWith('http://hub:5000', 'new');
    expect(hubApi.getGoalGovernanceSummary).toHaveBeenCalledWith('http://hub:5000', 'new');
    expect(facade.state.goals.map(goal => goal.id)).toEqual(['new', 'created', 'old']);
    expect(facade.state.selectedGoalId).toBe('new');
    expect(facade.state.goalDetail).toEqual(detail);
    expect(facade.state.goalGovernance).toEqual(governance);
    expect(facade.state.loading).toBe(false);
    expect(facade.recentGoals(2).map(goal => goal.id)).toEqual(['new', 'created']);
    expect(facade.costTasks().map((task: any) => task.id)).toEqual(['expensive', 'cheap']);
  });

  it('falls back to the newest goal and counts active goals', () => {
    const { facade } = setup();

    facade.refresh('http://hub:5000', 'missing');

    facade.state.goals[0].status = 'in_progress';
    facade.state.goals[1].status = 'completed';
    facade.state.goals[2].status = 'cancelled';
    expect(facade.state.selectedGoalId).toBe('new');
    expect(facade.activeGoalCount()).toBe(1);
  });

  it('resets state and reports a notification when the goal list fails', () => {
    const { facade, notifications } = setup({
      listGoals: vi.fn(() => throwError(() => new Error('offline'))),
    });

    facade.refresh('http://hub:5000', 'goal-1');

    expect(facade.state).toEqual({
      goals: [],
      selectedGoalId: '',
      goalDetail: null,
      goalGovernance: null,
      loading: false,
    });
    expect(notifications.error).toHaveBeenCalledWith('Goals konnten nicht geladen werden');
  });
});
