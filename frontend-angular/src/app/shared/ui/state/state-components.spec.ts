import { EmptyStateComponent } from './empty-state.component';
import { ErrorStateComponent } from './error-state.component';
import { LoadingStateComponent } from './loading-state.component';
import { StatusBadgeComponent } from './status-badge.component';
import { MetricCardComponent } from '../display/metric-card.component';
import { KeyValueGridComponent } from '../display/key-value-grid.component';
import { SectionCardComponent } from '../layout/section-card.component';

describe('shared state components', () => {
  it('keeps empty state inputs domain-neutral', () => {
    const cmp = new EmptyStateComponent();
    cmp.title = 'Keine Eintraege';
    cmp.description = 'Starte mit einer Aktion.';
    cmp.primaryLabel = 'Starten';
    cmp.primaryRouterLink = ['/dashboard'];

    expect(cmp.title).toBe('Keine Eintraege');
    expect(cmp.primaryRouterLink).toEqual(['/dashboard']);
  });

  it('emits retry actions from the error state', () => {
    const cmp = new ErrorStateComponent();
    const events: string[] = [];
    cmp.retry.subscribe(() => events.push('retry'));

    cmp.retry.emit();

    expect(events).toEqual(['retry']);
  });

  it('keeps loading state skeleton configuration reusable', () => {
    const cmp = new LoadingStateComponent();
    cmp.label = 'Lade Daten';
    cmp.count = 3;
    cmp.columns = 3;

    expect(cmp.label).toBe('Lade Daten');
    expect(cmp.count).toBe(3);
    expect(cmp.columns).toBe(3);
  });

  it('maps status badge tones to stable classes', () => {
    const cmp = new StatusBadgeComponent();
    cmp.tone = 'warning';

    expect(cmp.toneClass()).toBe('status-warning');
  });

  it('keeps layout and display primitives domain-neutral', () => {
    const section = new SectionCardComponent();
    section.title = 'Abschnitt';

    const metric = new MetricCardComponent();
    metric.label = 'Anzahl';
    metric.value = 4;
    metric.tone = 'success';

    const grid = new KeyValueGridComponent();
    grid.items = [{ label: 'Status', value: 'ok' }];

    expect(section.title).toBe('Abschnitt');
    expect(metric.toneClass()).toBe('metric-success');
    expect(grid.items[0].value).toBe('ok');
  });
});
