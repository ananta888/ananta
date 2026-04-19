import { SummaryPanelComponent } from './summary-panel.component';
import { TableShellComponent } from './table-shell.component';
import { ExplanationNoticeComponent } from './explanation-notice.component';
import { NextStepsComponent } from './next-steps.component';
import { SafetyNoticeComponent } from './safety-notice.component';
import { MetricCardComponent } from './metric-card.component';
import { KeyValueGridComponent } from './key-value-grid.component';
import { DecisionExplanationComponent } from './decision-explanation.component';

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

  it('keeps metric and key-value display inputs reusable across domains', () => {
    const metric = new MetricCardComponent();
    metric.label = 'Offene Arbeit';
    metric.value = 3;
    metric.hint = 'Noch nicht abgeschlossen.';
    metric.tone = 'warning';

    const grid = new KeyValueGridComponent();
    grid.items = [
      { label: 'Status', value: 'ok' },
      { label: 'Stand', value: 'heute' },
    ];
    grid.columns = 2;

    expect(metric.toneClass()).toBe('metric-warning');
    expect(grid.items.map(item => item.label)).toEqual(['Status', 'Stand']);
    expect(grid.columns).toBe(2);
  });

  it('provides reusable decision explanations with overridable copy', () => {
    const cmp = new DecisionExplanationComponent();
    cmp.kind = 'routing';

    expect(cmp.titleText()).toBe('Warum Zuweisung?');
    expect(cmp.messageText()).toContain('weist Arbeit gezielt zu');

    cmp.title = 'Warum wird gestoppt?';
    cmp.message = 'Eigene Grenze.';

    expect(cmp.titleText()).toBe('Warum wird gestoppt?');
    expect(cmp.messageText()).toBe('Eigene Grenze.');
  });
});
