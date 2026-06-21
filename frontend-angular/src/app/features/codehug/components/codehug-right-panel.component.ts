import { Component, ChangeDetectionStrategy } from '@angular/core';

/**
 * Rechte Spalte: Kontext-Paket + Agenten-Status. Wird per
 * <ng-content select="[chRight]"> in die Shell projiziert.
 */
@Component({
  selector: 'ch-right-panel',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section aria-label="Kontext und Agenten-Status">
      <h4 class="ch-panel-title">Kontextpaket</h4>
      <p class="ch-panel-empty">Kontextauswahl folgt in CH-003-001.</p>

      <h4 class="ch-panel-title">Agenten-Status</h4>
      <p class="ch-panel-empty">Kein aktiver Run.</p>
    </section>
  `,
  styles: [`
    :host { display: block; font-size: 12px; }
    .ch-panel-title {
      margin: 12px 0 6px;
      font-size: 11px;
      letter-spacing: 0.6px;
      text-transform: uppercase;
      color: var(--muted);
    }
    .ch-panel-empty { color: var(--muted); margin: 0 0 4px; font-size: 12px; }
  `]
})
export class CodeHugRightPanelComponent {}