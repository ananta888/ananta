import { SimpleChange } from '@angular/core';

import { NextStepAction } from '../shared/ui/display';
import { DashboardGuidedGoalWizardComponent, GoalModeDefinition } from './dashboard-guided-goal-wizard.component';
import { DashboardQuickGoalPanelComponent } from './dashboard-quick-goal-panel.component';

describe('dashboard quick goal flow', () => {
  it('keeps flow inputs stable and emits user actions through explicit outputs', () => {
    const component = new DashboardQuickGoalPanelComponent();
    const nextStep: NextStepAction = { id: 'open-board', label: 'Board oeffnen' };
    const seen: Record<string, unknown> = {};
    component.text = 'Repository analysieren';
    component.busy = false;
    component.result = { tasks_created: 2, task_ids: ['task-1', 'task-2'], goal_id: 'goal-1' };
    component.presets = [{ id: 'review', title: 'Review', description: 'Aenderungen pruefen' }];
    component.nextSteps = [nextStep];
    component.textChange.subscribe(value => seen['text'] = value);
    component.selectPreset.subscribe(value => seen['preset'] = value);
    component.submit.subscribe(() => seen['submit'] = true);
    component.openGoal.subscribe(value => seen['goal'] = value);
    component.openBoard.subscribe(() => seen['board'] = true);
    component.selectNextStep.subscribe(value => seen['step'] = value);
    component.dismissHint.subscribe(() => seen['hint'] = true);

    component.textChange.emit('Neue Aufgabe');
    component.selectPreset.emit('review');
    component.submit.emit();
    component.openGoal.emit('goal-1');
    component.openBoard.emit();
    component.selectNextStep.emit(nextStep);
    component.dismissHint.emit();

    expect(component.result.tasks_created).toBe(2);
    expect(seen).toEqual({
      text: 'Neue Aufgabe',
      preset: 'review',
      submit: true,
      goal: 'goal-1',
      board: true,
      step: nextStep,
      hint: true,
    });
  });
});

describe('dashboard guided goal flow', () => {
  const mode: GoalModeDefinition = {
    id: 'diagnosis',
    title: 'Diagnose',
    description: 'Fehler strukturiert untersuchen',
    fields: [
      { name: 'goal', label: 'Ziel', type: 'textarea' },
      { name: 'team_id', label: 'Team', type: 'select', options: ['frontend'], default: 'frontend' },
      { name: 'hidden_policy', label: 'Policy', type: 'hidden', default: 'safe' },
    ],
  };

  it('initializes defaults and validates every wizard phase before continuing', () => {
    const component = new DashboardGuidedGoalWizardComponent();

    component.setGoalMode(mode);

    expect(component.selectedGoalMode).toBe(mode);
    expect(component.goalModeData['team_id']).toBe('frontend');
    expect(component.goalModeData['execution_depth']).toBe('standard');
    expect(component.goalModeData['safety_level']).toBe('balanced');
    expect(component.requiredGoalFields().map(field => field.name)).toEqual(['goal', 'team_id']);
    expect(component.canContinueGoalWizard()).toBe(false);

    component.goalModeData['goal'] = 'Fehlerbild analysieren';
    expect(component.canContinueGoalWizard()).toBe(true);
    component.nextGoalWizardStep();
    expect(component.activeGoalWizardStep().id).toBe('context');

    component.nextGoalWizardStep();
    expect(component.activeGoalWizardStep().id).toBe('execution');
    component.goalModeData['execution_depth'] = '';
    expect(component.canContinueGoalWizard()).toBe(false);
    component.goalModeData['execution_depth'] = 'deep';
    component.nextGoalWizardStep();
    expect(component.activeGoalWizardStep().id).toBe('safety');

    component.goalModeData['safety_level'] = '';
    expect(component.canContinueGoalWizard()).toBe(false);
    component.goalModeData['safety_level'] = 'safe';
    component.nextGoalWizardStep();
    expect(component.activeGoalWizardStep().id).toBe('review');
    expect(component.selectedExecutionDepthLabel()).toBe('Gruendlich');
    expect(component.selectedSafetyLevelLabel()).toBe('Vorsichtig');
  });

  it('emits a copied guided goal payload and resets on resetKey changes', () => {
    const component = new DashboardGuidedGoalWizardComponent();
    let submitted: unknown = null;
    component.submitGoal.subscribe(value => submitted = value);

    component.setGoalMode(mode);
    component.goalModeData['goal'] = 'Tests planen';
    component.submitGuidedGoal();
    component.goalModeData['goal'] = 'Nachtraeglich geaendert';

    expect(submitted).toEqual({
      mode,
      modeData: expect.objectContaining({ goal: 'Tests planen', team_id: 'frontend' }),
    });

    component.ngOnChanges({
      resetKey: new SimpleChange(1, 2, false),
    });

    expect(component.selectedGoalMode).toBeNull();
    expect(component.goalModeData).toEqual({});
    expect(component.goalWizardStepIndex).toBe(0);
  });
});
