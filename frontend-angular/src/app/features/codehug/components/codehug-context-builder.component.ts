import { Component, ChangeDetectionStrategy } from '@angular/core';

/**
 * Placeholder fuer /codehug/context (CH-003).
 */
@Component({
  selector: 'ch-context-builder',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-placeholder">
      <h2>Kontext-Builder</h2>
      <p>Kontextauswahl, Vorschlaege, Export und Speicherung folgen in CH-003.</p>
    </section>
  `,
  styles: [`
    :host { display: block; padding: 18px; }
    .ch-placeholder h2 { margin: 0 0 6px; font-size: 18px; }
    .ch-placeholder p { color: var(--muted); margin: 0; font-size: 13px; }
  `]
})
export class CodeHugContextBuilderComponent {}