import { EmptyStateComponent } from './empty-state.component';
import { ErrorStateComponent } from './error-state.component';

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
});
