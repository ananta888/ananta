import { SummaryPanelComponent } from './summary-panel.component';
import { TableShellComponent } from './table-shell.component';

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
});
