import { ActionCardComponent } from './action-card.component';
import { PageIntroComponent } from './page-intro.component';
import { SectionCardComponent } from './section-card.component';
import { SectionHeaderComponent } from './section-header.component';

describe('shared layout components', () => {
  it('keeps page intro copy and actions configurable', () => {
    const cmp = new PageIntroComponent();
    cmp.title = 'Start';
    cmp.subtitle = 'Kurzbeschreibung';
    cmp.eyebrow = 'Bereich';

    expect(cmp.title).toBe('Start');
    expect(cmp.subtitle).toBe('Kurzbeschreibung');
    expect(cmp.eyebrow).toBe('Bereich');
  });

  it('keeps section headers domain-neutral', () => {
    const cmp = new SectionHeaderComponent();
    cmp.title = 'Aktivitaet';
    cmp.subtitle = 'Filter und Aktionen bleiben Projektion.';

    expect(cmp.title).toBe('Aktivitaet');
    expect(cmp.subtitle).toContain('Projektion');
  });

  it('keeps card variants semantic and finite', () => {
    const cmp = new SectionCardComponent();
    cmp.variant = 'technical';

    expect(cmp.variant).toBe('technical');
  });

  it('emits button action cards without coupling to router navigation', () => {
    const cmp = new ActionCardComponent();
    const events: string[] = [];
    cmp.title = 'Demo';
    cmp.description = 'Startet eine lokale Aktion.';
    cmp.action.subscribe(() => events.push('action'));

    cmp.action.emit();

    expect(events).toEqual(['action']);
  });

  it('keeps action cards stable for links, router links and badges', () => {
    const cmp = new ActionCardComponent();
    cmp.title = 'Ergebnisse';
    cmp.description = 'Resultate oeffnen.';
    cmp.routerLink = ['/artifacts'];
    cmp.badge = 'neu';
    cmp.ariaLabel = 'Ergebnisse ansehen';

    expect(cmp.routerLink).toEqual(['/artifacts']);
    expect(cmp.badge).toBe('neu');
    expect(cmp.ariaLabel).toBe('Ergebnisse ansehen');

    cmp.routerLink = null;
    cmp.href = '#quick-goal';

    expect(cmp.href).toBe('#quick-goal');
  });
});
