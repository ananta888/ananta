import { Component, ChangeDetectionStrategy } from '@angular/core';

/**
 * Placeholder fuer /codehug/internals (CH-014).
 * Volle Topologie/Trace/Konfig-Anzeige folgt in CH-014.
 */
@Component({
  selector: 'ch-internals',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="ch-placeholder">
      <h2>System Internals</h2>
      <p>Hub/Worker-Topologie, Trace-Anzeige (Simplified / Details / Raw), Layer-Konfiguration und Live-Edit folgen in CH-014.</p>
    </section>
  `,
  styles: [`
    :host { display: block; padding: 18px; }
    .ch-placeholder h2 { margin: 0 0 6px; font-size: 18px; }
    .ch-placeholder p { color: var(--muted); margin: 0; font-size: 13px; }
  `]
})
export class CodeHugInternalsComponent {}