import { Component, ChangeDetectionStrategy } from '@angular/core';

/**
 * Placeholder fuer /codehug/agents (CH-005/006).
 */
@Component({
  selector: 'ch-agents',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-placeholder">
      <h2>Agenten</h2>
      <p>Profil-Auswahl, Agenten-Run, Diff-Vorschau und Apply folgen in CH-005 und CH-006.</p>
    </section>
  `,
  styles: [`
    :host { display: block; padding: 18px; }
    .ch-placeholder h2 { margin: 0 0 6px; font-size: 18px; }
    .ch-placeholder p { color: var(--muted); margin: 0; font-size: 13px; }
  `]
})
export class CodeHugAgentsComponent {}