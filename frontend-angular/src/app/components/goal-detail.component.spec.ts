import { GoalDetailComponent } from './goal-detail.component.ts';

describe('GoalDetailComponent result summary', () => {
  function component(): GoalDetailComponent {
    return Object.create(GoalDetailComponent.prototype) as GoalDetailComponent;
  }

  it('summarizes completed goals with artifacts as finished', () => {
    const cmp = component();
    cmp.goal = { status: 'completed' };
    cmp.tasks = [{ status: 'completed' }, { status: 'completed' }];
    cmp.artifacts = [{ title: 'Result', preview: 'Done' }];
    cmp.artifactSummary = { headline_artifact: { title: 'Headline', preview: 'Summary' } };
    cmp.governance = { verification: { passed: 2, total: 2 } };

    expect(cmp.resultHeadline()).toBe('Goal abgeschlossen');
    expect(cmp.completedTasks()).toBe(2);
    expect(cmp.openTasks()).toBe(0);
    expect(cmp.verificationLabel()).toBe('2/2');
    expect(cmp.headlineArtifact().title).toBe('Headline');
  });

  it('surfaces failed tasks as attention-needed result state', () => {
    const cmp = component();
    cmp.goal = { status: 'running' };
    cmp.tasks = [{ status: 'completed' }, { status: 'failed' }, { status: 'todo' }];
    cmp.artifacts = [];
    cmp.artifactSummary = null;
    cmp.governance = null;

    expect(cmp.resultHeadline()).toBe('Goal braucht Aufmerksamkeit');
    expect(cmp.failedTasks()).toBe(1);
    expect(cmp.openTasks()).toBe(1);
    expect(cmp.resultDescription()).toContain('fehlgeschlagen');
    expect(cmp.resultSafetyExplanation()).toContain('Logs');
  });

  it('explains open verification and incomplete work as safety boundaries', () => {
    const cmp = component();
    cmp.goal = { status: 'running' };
    cmp.tasks = [{ status: 'completed' }, { status: 'todo' }];
    cmp.artifacts = [];
    cmp.artifactSummary = null;
    cmp.governance = null;

    expect(cmp.resultSafetyExplanation()).toContain('offene Tasks');

    cmp.tasks = [{ status: 'completed' }];
    expect(cmp.resultSafetyExplanation()).toContain('Pruefhinweise');
  });
});
