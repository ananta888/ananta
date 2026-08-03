import { ChangeDetectionStrategy, Component, Input } from '@angular/core';

import { OrganizationGraphProjection } from './organization-graph-projection.service';

@Component({
  selector: 'app-organization-graph-legend',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <aside class="legend" aria-labelledby="organization-3d-legend-heading">
      <h3 id="organization-3d-legend-heading">Legende</h3>
      <section>
        <h4>Knotenfarbe · {{ projection.activeMetrics.nodeColor }}</h4>
        <ul>
          @for (entry of projection.nodeLegend; track entry.key) {
            <li><i [style.background]="entry.color"></i><span>{{ entry.label }}</span><small>{{ entry.count }}</small></li>
          }
        </ul>
      </section>
      <section>
        <h4>Knotengröße · {{ projection.sizeLegend.metric }}</h4>
        <ul>
          @for (entry of projection.sizeLegend.references; track entry.label) {
            <li><span class="size" [style.width.px]="entry.value" [style.height.px]="entry.value"></span><span>{{ entry.label }}</span><small>{{ entry.value.toFixed(1) }}</small></li>
          }
        </ul>
      </section>
      <section>
        <h4>
          Kantenfarbe · {{ projection.activeMetrics.edgeColor }} ·
          Kantenstärke · {{ projection.activeMetrics.edgeStrength }}
        </h4>
        <ul>
          @for (entry of projection.edgeLegend; track entry.key) {
            <li>
              <i class="edge" [style.border-color]="entry.color" [style.border-width.px]="entry.maximumWidth"></i>
              <span>{{ entry.label }}</span>
              <small>{{ entry.count }} · min {{ entry.minimumWidth.toFixed(1) }} / median {{ entry.medianWidth.toFixed(1) }} / max {{ entry.maximumWidth.toFixed(1) }}</small>
            </li>
          }
        </ul>
      </section>
    </aside>
  `,
  styles: [`
    .legend { background: #0d1728; border: 1px solid #304665; border-radius: .55rem; display: grid; gap: .55rem; padding: .65rem; }
    h3, h4 { margin: 0; } h3 { font-size: .9rem; } h4 { color: #9eb6d6; font-size: .72rem; }
    section { display: grid; gap: .3rem; }
    ul { display: flex; flex-wrap: wrap; gap: .35rem .75rem; list-style: none; margin: 0; padding: 0; }
    li { align-items: center; display: grid; font-size: .7rem; gap: .3rem; grid-template-columns: auto minmax(70px, auto) auto; }
    i { border: 1px solid rgba(255,255,255,.5); border-radius: 50%; display: inline-block; height: .75rem; width: .75rem; }
    i.edge { border-radius: 0; border-style: solid none none; height: 0; width: 1.8rem; }
    .size { background: #8b5cf6; border-radius: 50%; display: inline-block; max-height: 24px; max-width: 24px; min-height: 5px; min-width: 5px; }
    small { color: #8398b7; }
  `],
})
export class OrganizationGraphLegendComponent {
  @Input({ required: true }) projection!: OrganizationGraphProjection;
}
