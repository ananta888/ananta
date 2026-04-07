import { of } from 'rxjs';

import { AutoPlannerComponent } from './auto-planner.component.ts';

describe('AutoPlannerComponent', () => {
  function createComponent(): AutoPlannerComponent {
    const cmp = Object.create(AutoPlannerComponent.prototype) as AutoPlannerComponent & {
      controlPlane: any;
      cdr: any;
      ns: any;
      loadGoals: any;
      selectGoal: any;
    };

    Object.defineProperty(cmp, 'controlPlane', {
      value: { createGoal: vi.fn(() => of({ goal: { id: 'G-1' }, subtasks: [], created_task_ids: [] })) },
      configurable: true,
      writable: true,
    });
    Object.defineProperty(cmp, 'cdr', {
      value: { detectChanges: vi.fn() },
      configurable: true,
      writable: true,
    });
    Object.defineProperty(cmp, 'ns', {
      value: { success: vi.fn(), error: vi.fn(), fromApiError: vi.fn((_e: any, fallback: string) => fallback) },
      configurable: true,
      writable: true,
    });

    cmp.hub = { role: 'hub', url: 'http://hub:5000' } as any;
    cmp.goalForm = {
      goal: 'Goal',
      context: 'ctx',
      team_id: 'team-1',
      create_tasks: true,
      constraintsText: 'constraint-a\nconstraint-b',
      acceptanceCriteriaText: 'criterion-a',
      securityLevel: 'strict',
      routingPreference: 'active_team_or_hub_default',
    };
    cmp.advancedMode = true;
    cmp.isAdmin = false;
    cmp.planning = false;
    cmp.planningResult = null;
    cmp.selectedGoalId = '';
    cmp.loadGoals = vi.fn();
    cmp.selectGoal = vi.fn();
    Object.defineProperty(cmp, 'dir', {
      value: {
        list: vi.fn(() => [
          { name: 'hub', url: 'http://hub:5000', role: 'hub' },
          { name: 'alpha', url: 'http://alpha:5000', role: 'worker' },
          { name: 'beta', url: 'http://beta:5000', role: 'worker' },
        ]),
      },
      configurable: true,
      writable: true,
    });
    cmp.teams = [];
    return cmp;
  }

  it('keeps safe defaults for non-admin advanced goal planning', () => {
    const cmp = createComponent();

    cmp.planGoal();

    expect(cmp.controlPlane.createGoal).toHaveBeenCalledTimes(1);
    const body = cmp.controlPlane.createGoal.mock.calls[0][1];
    expect(body.goal).toBe('Goal');
    expect(body.constraints).toEqual(['constraint-a', 'constraint-b']);
    expect(body.acceptance_criteria).toEqual(['criterion-a']);
    expect(body.workflow).toBeUndefined();
  });

  it('includes admin workflow controls when admin uses advanced mode', () => {
    const cmp = createComponent();
    cmp.isAdmin = true;

    cmp.planGoal();

    expect(cmp.controlPlane.createGoal).toHaveBeenCalledTimes(1);
    const body = cmp.controlPlane.createGoal.mock.calls[0][1];
    expect(body.workflow).toEqual({
      routing: { mode: 'active_team_or_hub_default' },
      policy: { security_level: 'strict' },
    });
  });

  it('prefers the active team with known worker members for goal defaults', () => {
    const cmp = createComponent();
    cmp.teams = [
      { id: 'team-empty', name: 'Empty Team', is_active: false, members: [] },
      { id: 'team-active', name: 'Active Team', is_active: true, members: [{ agent_url: 'http://alpha:5000' }] },
      { id: 'team-other', name: 'Other Team', is_active: false, members: [{ agent_url: 'http://beta:5000' }] },
    ];

    expect(cmp.resolvePreferredTeamId('')).toBe('team-active');
  });

  it('keeps the current valid team selection unchanged', () => {
    const cmp = createComponent();
    cmp.teams = [
      { id: 'team-empty', name: 'Empty Team', is_active: false, members: [] },
      { id: 'team-active', name: 'Active Team', is_active: true, members: [{ agent_url: 'http://alpha:5000' }] },
    ];

    expect(cmp.resolvePreferredTeamId('team-empty')).toBe('team-empty');
  });
});
