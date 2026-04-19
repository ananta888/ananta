import { SummaryPanelComponent } from './summary-panel.component';
import { TableShellComponent } from './table-shell.component';
import { ExplanationNoticeComponent } from './explanation-notice.component';
import { NextStepsComponent } from './next-steps.component';
import { SafetyNoticeComponent } from './safety-notice.component';

describe('shared display components', () => {
  it('keeps summary panel metrics domain-neutral', () => {
    const cmp = new SummaryPanelComponent();
    cmp.title = 'Zusammenfassung';
    cmp.metrics = [
      { label: 'Versionen', value: 2 },
      { label: 'Status', value: 'ok', tone: 'success' },
    ];

    expect(cmp.metrics[0].value).toBe(2);
    expect(cmp.metrics[1].tone).toBe('success');
  });

  it('supports table loading empty and refresh states', () => {
    const cmp = new TableShellComponent();
    const events: string[] = [];
    cmp.title = 'Versionen';
    cmp.loading = true;
    cmp.empty = false;
    cmp.refreshLabel = 'Neu laden';
    cmp.refresh.subscribe(() => events.push('refresh'));

    cmp.refresh.emit();

    expect(cmp.loading).toBe(true);
    expect(events).toEqual(['refresh']);
  });

  it('emits local next step actions while keeping links declarative', () => {
    const cmp = new NextStepsComponent();
    const selected: string[] = [];
    cmp.steps = [
      { id: 'refresh', label: 'Aktualisieren' },
      { id: 'board', label: 'Board', routerLink: ['/board'] },
    ];
    cmp.selectStep.subscribe(step => selected.push(step.id));

    cmp.selectStep.emit(cmp.steps[0]);

    expect(selected).toEqual(['refresh']);
    expect(cmp.steps[1].routerLink).toEqual(['/board']);
  });

  it('keeps explanation and safety notices semantic', () => {
    const explanation = new ExplanationNoticeComponent();
    explanation.tone = 'technical';

    const safety = new SafetyNoticeComponent();
    safety.message = 'Review erforderlich.';
    safety.tone = 'warning';

    expect(explanation.toneClass()).toBe('notice-technical');
    expect(safety.message).toContain('Review');
  });
});
