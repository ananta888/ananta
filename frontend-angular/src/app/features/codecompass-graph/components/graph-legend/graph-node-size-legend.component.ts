import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

import { GraphNodeSizeLegendModel } from './graph-legend.models';

@Component({
  standalone: true,
  selector: 'app-graph-node-size-legend',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="size-legend" aria-label="Knotengröße">
      <h3>Knotengröße</h3>
      @if (!model) {
        <p class="muted">Keine Größenprojektion verfügbar.</p>
      } @else {
        <p class="profile">Profil: {{ model.profileName }}</p>
        <div class="size-references" aria-label="Größenreferenzen">
          @for (reference of model.references; track reference.label) {
            <div class="size-reference">
              <span
                class="node-sample"
                aria-hidden="true"
                [style.width.px]="reference.value"
                [style.height.px]="reference.value"
              ></span>
              <span>{{ reference.label }} · {{ reference.value.toFixed(1) }}</span>
            </div>
          }
        </div>
        @if (model.metricsVisible) {
        <ul class="metric-list" aria-label="Aktive Knotenmetriken">
          @for (metric of model.metrics; track metric.metricId) {
            <li>
              <span class="metric-label">{{ metric.label }}</span>
              <span>Gewicht {{ metric.weight }}</span>
              <span class="availability" [attr.data-availability]="metric.availability">
                {{ availabilityLabel(metric.availability) }}
                @if (metric.reasonCode) { · {{ metric.reasonCode }} }
              </span>
            </li>
          } @empty {
            <li class="muted">Fallback-Minimalgröße – keine aktive verfügbare Metrik</li>
          }
        </ul>
        }
      }
    </section>
  `,
  styles: [`
    :host { display: block; }
    h3 { margin: 0 0 .45rem; font-size: .85rem; }
    .profile, .muted { margin: .25rem 0; color: #64748b; font-size: .75rem; }
    .size-references { display: flex; align-items: end; gap: .75rem; min-height: 48px; margin: .4rem 0; }
    .size-reference { display: flex; flex-direction: column; align-items: center; gap: .2rem; font-size: .68rem; color: #475569; }
    .node-sample { display: block; min-width: 8px; min-height: 8px; max-width: 42px; max-height: 42px; border-radius: 50%; background: #64748b; }
    .metric-list { list-style: none; padding: 0; margin: .4rem 0 0; display: grid; gap: .3rem; }
    .metric-list li { display: grid; grid-template-columns: minmax(0, 1fr) auto; column-gap: .45rem; font-size: .7rem; }
    .metric-label { font-weight: 600; }
    .availability { grid-column: 1 / -1; color: #64748b; }
    [data-availability='unavailable'] { color: #b45309; }
    [data-availability='approximate'] { color: #0369a1; }
  `],
})
export class GraphNodeSizeLegendComponent {
  @Input() model: GraphNodeSizeLegendModel | null = null;

  availabilityLabel(value: string): string {
    return ({
      available: 'verfügbar',
      approximate: 'näherungsweise',
      unavailable: 'nicht verfügbar',
      not_applicable: 'nicht anwendbar',
    } as Record<string, string>)[value] ?? value;
  }
}
