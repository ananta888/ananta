import {
  ChangeDetectionStrategy,
  Component,
  Input,
  computed,
  signal,
} from '@angular/core';

import {
  MODEL_ANALYSIS_MAX_GRAPH_EDGES,
  MODEL_ANALYSIS_MAX_GRAPH_NODES,
} from './model-analysis-api.client';
import { ModelAnalysisGraph } from './model-analysis.models';

@Component({
  selector: 'app-model-analysis-graph',
  standalone: true,
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="model-graph" aria-labelledby="model-graph-title">
      <header>
        <div>
          <p class="eyebrow">Bounded topology</p>
          <h3 id="model-graph-title">Modellgraph</h3>
        </div>
        <p class="counts" aria-label="Graphumfang">
          {{ nodes().length }} Knoten · {{ edges().length }} Kanten
        </p>
      </header>

      @if (!graph || nodes().length === 0) {
        <p class="graph-empty" role="status">Keine Graphdaten verfügbar.</p>
      } @else {
        @if (isTruncated()) {
          <p class="graph-limit" role="status">
            Ansicht wurde auf {{ maxNodes }} Knoten und {{ maxEdges }} Kanten begrenzt.
          </p>
        }
        <div class="node-grid" role="list" aria-label="Modellgraph-Knoten">
          @for (node of nodes(); track node.node_id) {
            <article class="node" role="listitem">
              <span class="node-kind">{{ node.kind }}</span>
              <strong>{{ node.label }}</strong>
              <code>{{ node.node_id }}</code>
            </article>
          }
        </div>
        <details>
          <summary>Kantenliste anzeigen</summary>
          <table>
            <caption class="sr-only">Gerichtete Kanten des Modellgraphen</caption>
            <thead><tr><th scope="col">Von</th><th scope="col">Typ</th><th scope="col">Nach</th></tr></thead>
            <tbody>
              @for (edge of edges(); track edge.edge_id) {
                <tr>
                  <td><code>{{ edge.source_node_id }}</code></td>
                  <td>{{ edge.kind }}</td>
                  <td><code>{{ edge.target_node_id }}</code></td>
                </tr>
              }
            </tbody>
          </table>
        </details>
      }
    </section>
  `,
  styles: [`
    :host{display:block}.model-graph{border:1px solid #9ca89f;background:#f5f1e7;padding:1rem}
    header{display:flex;justify-content:space-between;gap:1rem;align-items:end;border-bottom:2px solid #18372d;padding-bottom:.75rem}
    h3{font:700 1.45rem Georgia,serif;margin:0;color:#18372d}.eyebrow,.node-kind{font:700 .7rem "Courier New",monospace;letter-spacing:.12em;text-transform:uppercase}
    .eyebrow{color:#a33b20;margin:0 0 .25rem}.counts{font-weight:700;margin:0}.node-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(13rem,1fr));gap:.65rem;margin:1rem 0}
    .node{background:#fff;border-left:5px solid #1c6e78;box-shadow:2px 2px 0 #18372d;display:grid;gap:.3rem;padding:.75rem;min-width:0}
    .node-kind{color:#a33b20}.node code,td code{font-size:.72rem;overflow-wrap:anywhere}.graph-limit{border:1px solid #a33b20;background:#fff4de;padding:.65rem}
    details{margin-top:1rem}summary{cursor:pointer;font-weight:700}table{border-collapse:collapse;width:100%;margin-top:.75rem}th,td{border:1px solid #9ca89f;padding:.5rem;text-align:left}
    .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}
    @media(max-width:600px){header{align-items:start;flex-direction:column}.node-grid{grid-template-columns:1fr}table{font-size:.78rem}}
  `],
})
export class ModelAnalysisGraphComponent {
  private readonly graphState = signal<ModelAnalysisGraph | null>(null);

  @Input()
  set graph(value: ModelAnalysisGraph | null) {
    this.graphState.set(value);
  }

  get graph(): ModelAnalysisGraph | null {
    return this.graphState();
  }

  readonly maxNodes = MODEL_ANALYSIS_MAX_GRAPH_NODES;
  readonly maxEdges = MODEL_ANALYSIS_MAX_GRAPH_EDGES;
  readonly nodes = computed(() => (this.graphState()?.nodes || []).slice(0, this.maxNodes));
  readonly edges = computed(() => (this.graphState()?.edges || []).slice(0, this.maxEdges));
  readonly isTruncated = computed(() =>
    Boolean(this.graphState()?.truncated)
    || (this.graphState()?.nodes.length || 0) > this.nodes().length
    || (this.graphState()?.edges.length || 0) > this.edges().length,
  );
}
