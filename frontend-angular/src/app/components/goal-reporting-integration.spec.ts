import { TestBed } from '@angular/core/testing';
import { By } from '@angular/platform-browser';

import { DashboardGoalGovernanceSummaryCardComponent } from './dashboard-goal-governance-summary-card.component';
import { GoalDetailComponent } from './goal-detail.component.ts';

describe('goal reporting and governance integration', () => {
  afterEach(() => TestBed.resetTestingModule());

  it('renders loading, empty and populated governance reporting states', () => {
    TestBed.configureTestingModule({
      imports: [DashboardGoalGovernanceSummaryCardComponent],
    });
    // Loading state
    {
      const fixture = TestBed.createComponent(DashboardGoalGovernanceSummaryCardComponent);
      const component = fixture.componentInstance;
      component.loading = true;
      fixture.detectChanges();
      expect(fixture.nativeElement.textContent).toContain('Goal Governance & Cost Summary');
      expect(fixture.debugElement.query(By.css('app-ui-skeleton'))).not.toBeNull();
      fixture.destroy();
    }

    // Empty state
    {
      const fixture = TestBed.createComponent(DashboardGoalGovernanceSummaryCardComponent);
      const component = fixture.componentInstance;
      component.loading = false;
      fixture.detectChanges();
      expect(fixture.nativeElement.textContent).toContain('Noch keine Goals fuer Governance- und Cost-Reporting vorhanden.');
      fixture.destroy();
    }

    // Populated state (fresh fixture to avoid ExpressionChangedAfterItHasBeenCheckedError on ngModel/disabled transitions)
    {
      const fixture = TestBed.createComponent(DashboardGoalGovernanceSummaryCardComponent);
      const component = fixture.componentInstance;
      component.goals = [{ id: 'goal-1', summary: 'Release Goal', status: 'planned' }] as any;
      component.selectedGoalId = 'goal-1';
      component.goalDetail = {
        goal: { id: 'goal-1', summary: 'Release Goal', status: 'completed' },
        tasks: [
          {
            id: 'task-expensive',
            title: 'Teure Ausfuehrung',
            status: 'completed',
            verification_status: { status: 'passed' },
            cost_summary: { cost_units: 3.5, tokens_total: 1200 },
          },
        ],
      } as any;
      component.goalGovernance = {
        goal_id: 'goal-1',
        verification: { total: 2, passed: 2, failed: 0, escalated: 0 },
        policy: { approved: 2, blocked: 0 },
        cost_summary: { total_cost_units: 3.5, tasks_with_cost: 1, total_tokens: 1200, total_latency_ms: 850 },
        summary: { task_count: 1 },
      } as any;
      component.costTasks = (component.goalDetail as any).tasks || [];
      fixture.detectChanges();

      const text = fixture.nativeElement.textContent;
      expect(text).toContain('Release Goal');
      expect(text).toContain('2/2');
      expect(text).toContain('Approved');
      expect(text).toContain('Cost Units');
      expect(text).toContain('Teure Ausfuehrung');
      fixture.destroy();
    }
  });

  it('emits goal selection and refresh events from the governance card controls', () => {
    TestBed.configureTestingModule({
      imports: [DashboardGoalGovernanceSummaryCardComponent],
    });
    const fixture = TestBed.createComponent(DashboardGoalGovernanceSummaryCardComponent);
    const component = fixture.componentInstance;
    const events: Array<string | undefined> = [];
    component.goals = [
      { id: 'goal-1', summary: 'Erstes Goal' },
      { id: 'goal-2', summary: 'Zweites Goal' },
    ];
    component.selectedGoalId = 'goal-1';
    component.selectGoal.subscribe(goalId => events.push(`select:${goalId}`));
    component.refresh.subscribe(goalId => events.push(`refresh:${goalId}`));
    fixture.detectChanges();

    fixture.debugElement.query(By.css('select')).triggerEventHandler('ngModelChange', 'goal-2');
    fixture.debugElement.query(By.css('button')).triggerEventHandler('click');

    expect(events).toEqual(['select:goal-2', 'refresh:goal-1']);
  });

  it('combines result summary, artifact and governance helper states for goal detail', () => {
    const component = Object.create(GoalDetailComponent.prototype) as GoalDetailComponent;
    component.goal = { id: 'goal-1', status: 'completed', summary: 'Release Goal' };
    component.tasks = [
      { id: 'task-1', status: 'completed' },
      { id: 'task-2', status: 'completed' },
    ];
    component.artifacts = [{ title: 'Report', preview: 'Release report' }];
    component.artifactSummary = { headline_artifact: { title: 'Headline', preview: 'Result summary' } };
    component.governance = {
      verification: { passed: 2, total: 2 },
      policy: { approved: 2, blocked: 0 },
    };

    expect(component.resultHeadline()).toBe('Goal abgeschlossen');
    expect(component.resultDescription()).toContain('Ergebnisse');
    expect(component.resultSafetyExplanation()).toContain('Pruefschritte');
    expect(component.verificationLabel()).toBe('2/2');
    expect(component.headlineArtifact().title).toBe('Headline');

    component.tasks = [{ id: 'task-1', status: 'completed' }, { id: 'task-2', status: 'failed' }];
    component.artifacts = [];
    component.artifactSummary = null;

    expect(component.resultHeadline()).toBe('Goal braucht Aufmerksamkeit');
    expect(component.resultDescription()).toContain('fehlgeschlagen');
    expect(component.resultSafetyExplanation()).toContain('Logs');
  });
});
