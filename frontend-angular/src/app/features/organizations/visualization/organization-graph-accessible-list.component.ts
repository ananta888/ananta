import { ChangeDetectionStrategy, Component, EventEmitter, Input, Output } from '@angular/core';

import { RenderGraph } from '../../graph-rendering/models/render-graph.models';

@Component({
  selector: 'app-organization-graph-accessible-list',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <details class="fallback" [open]="open">
      <summary>Barrierefreie Graphliste · {{ graph.nodes.length }} Knoten, {{ graph.edges.length }} Kanten</summary>
      <div class="columns">
        <section aria-labelledby="organization-graph-node-list-heading">
          <h3 id="organization-graph-node-list-heading">Knoten</h3>
          <div class="items" role="list">
            @for (node of graph.nodes; track node.id) {
              <button
                type="button"
                role="listitem"
                [attr.aria-current]="selectedNodeId === node.id ? 'true' : null"
                [attr.aria-label]="node.tooltip"
                (click)="nodeSelected.emit(node.id)">
                <span>{{ node.kind }}</span><strong>{{ node.label }}</strong>
              </button>
            }
          </div>
        </section>
        <section aria-labelledby="organization-graph-edge-list-heading">
          <h3 id="organization-graph-edge-list-heading">Kanten</h3>
          <div class="items" role="list">
            @for (edge of graph.edges; track edge.id) {
              <button
                type="button"
                role="listitem"
                [attr.aria-current]="selectedEdgeId === edge.id ? 'true' : null"
                [attr.aria-label]="edge.tooltip"
                (click)="edgeSelected.emit(edge.id)">
                <span>{{ edge.kind }}</span><strong>{{ edge.label }}</strong>
              </button>
            }
          </div>
        </section>
      </div>
    </details>
  `,
  styles: [`
    .fallback { background: #0d1728; border: 1px solid #304665; border-radius: .55rem; padding: .55rem .7rem; }
    summary { cursor: pointer; font-weight: 700; }
    .columns { display: grid; gap: .7rem; grid-template-columns: repeat(2, minmax(0, 1fr)); margin-top: .55rem; }
    h3 { color: #9eb6d6; font-size: .78rem; margin: 0 0 .35rem; }
    .items { display: grid; gap: .25rem; max-height: 260px; overflow: auto; }
    button { align-items: start; background: #111f34; border: 1px solid #304665; border-radius: .35rem; color: #e5edf8; display: grid; gap: .1rem; padding: .4rem; text-align: left; }
    button[aria-current='true'] { border-color: #fbbf24; box-shadow: 0 0 0 2px #fbbf24; }
    button span { color: #8da6c7; font-size: .65rem; text-transform: uppercase; }
    button:focus-visible, summary:focus-visible { outline: 3px solid #7eb2f5; outline-offset: 2px; }
    @media (max-width: 800px) { .columns { grid-template-columns: 1fr; } }
  `],
})
export class OrganizationGraphAccessibleListComponent {
  @Input({ required: true }) graph!: RenderGraph;
  @Input() selectedNodeId: string | null = null;
  @Input() selectedEdgeId: string | null = null;
  @Input() open = false;
  @Output() nodeSelected = new EventEmitter<string>();
  @Output() edgeSelected = new EventEmitter<string>();
}
